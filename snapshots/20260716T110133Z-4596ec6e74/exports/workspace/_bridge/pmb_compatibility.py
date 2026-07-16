"""PMB package compatibility and index consistency governance.

Ownership: local PMB owner compatibility checks and bounded repairs.
Non-goals: PMB memory approval, event rewriting, daemon lifecycle, or package upgrades.
State behavior: read-only by default; package patching requires an explicit apply path.
Caller context: invoked by ``local_pmb_memory.py`` with the governed PMB venv and home.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SEARCH_VULNERABLE = """        self._bm25_ulids = []
        self._bm25_tokens = []
        self._bm25 = None

        n = 0
"""
SEARCH_FIXED = """        self._bm25_ulids = []
        self._bm25_tokens = []
        self._bm25 = None
        self._bm25_reloaded = True
        self._save_bm25_cache()

        n = 0
"""
WORKSPACE_VULNERABLE = """def list_workspaces(pmb_home: Path | None = None) -> list[Workspace]:
    \"\"\"List all existing workspaces.\"\"\"
    pmb_home = pmb_home or DEFAULT_PMB_HOME
"""
WORKSPACE_FIXED = """def list_workspaces(pmb_home: Path | None = None) -> list[Workspace]:
    \"\"\"List all existing workspaces.\"\"\"
    pmb_home = pmb_home or Path(os.environ.get(\"PMB_HOME\", DEFAULT_PMB_HOME))
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_json(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    stdout = proc.stdout.strip()
    try:
        payload = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        payload = {}
    return {
        "ok": proc.returncode == 0 and isinstance(payload, dict),
        "returncode": proc.returncode,
        "payload": payload,
        "stdout_preview": stdout[:2000],
        "stderr_preview": proc.stderr.strip()[:2000],
    }


def package_metadata(pmb_python: Path) -> dict[str, Any]:
    code = (
        "import inspect,json,pmb,pmb.core.search,pmb.core.workspace;"
        "print(json.dumps({'version':getattr(pmb,'__version__','unknown'),"
        "'search_path':inspect.getsourcefile(pmb.core.search),"
        "'workspace_path':inspect.getsourcefile(pmb.core.workspace)}))"
    )
    result = _run_json([str(pmb_python), "-c", code], timeout=60)
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    return {
        "ok": bool(result.get("ok")) and bool(payload.get("search_path")) and bool(payload.get("workspace_path")),
        "version": payload.get("version"),
        "search_path": payload.get("search_path"),
        "workspace_path": payload.get("workspace_path"),
        "error": result.get("stderr_preview") or result.get("error"),
    }


def package_patch_state(metadata: dict[str, Any]) -> dict[str, Any]:
    search_path = Path(str(metadata.get("search_path") or ""))
    workspace_path = Path(str(metadata.get("workspace_path") or ""))
    if not search_path.is_file() or not workspace_path.is_file():
        return {"ok": False, "reason": "package_source_missing", "search_path": str(search_path), "workspace_path": str(workspace_path)}
    search_text = search_path.read_text(encoding="utf-8")
    workspace_text = workspace_path.read_text(encoding="utf-8")
    search_fixed = SEARCH_FIXED in search_text
    workspace_fixed = WORKSPACE_FIXED in workspace_text
    return {
        "ok": search_fixed and workspace_fixed,
        "version": metadata.get("version"),
        "search": {"path": str(search_path), "sha256": sha256_path(search_path), "fixed": search_fixed, "vulnerable_signature": SEARCH_VULNERABLE in search_text},
        "workspace": {"path": str(workspace_path), "sha256": sha256_path(workspace_path), "fixed": workspace_fixed, "vulnerable_signature": WORKSPACE_VULNERABLE in workspace_text},
    }


def _atomic_write(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", delete=False, dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp") as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def apply_package_fixes(pmb_python: Path, *, apply: bool) -> dict[str, Any]:
    metadata = package_metadata(pmb_python)
    if not metadata.get("ok"):
        return {"schema": "pmb-compatibility.apply.v1", "ok": False, "reason": "package_metadata_failed", "metadata": metadata}
    before = package_patch_state(metadata)
    if not apply:
        return {"schema": "pmb-compatibility.apply.v1", "ok": True, "applied": False, "reason": "explicit_apply_required", "before": before}
    changes: list[dict[str, Any]] = []
    for key, vulnerable, fixed in (("search", SEARCH_VULNERABLE, SEARCH_FIXED), ("workspace", WORKSPACE_VULNERABLE, WORKSPACE_FIXED)):
        info = before.get(key) if isinstance(before.get(key), dict) else {}
        path = Path(str(info.get("path") or ""))
        if info.get("fixed"):
            changes.append({"target": key, "changed": False, "reason": "already_fixed", "path": str(path)})
            continue
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if text.count(vulnerable) != 1:
            return {"schema": "pmb-compatibility.apply.v1", "ok": False, "reason": "source_signature_changed", "target": key, "path": str(path), "match_count": text.count(vulnerable), "version": metadata.get("version"), "changes": changes}
        _atomic_write(path, text.replace(vulnerable, fixed, 1))
        changes.append({"target": key, "changed": True, "path": str(path), "sha256": sha256_path(path)})
    after = package_patch_state(metadata)
    return {"schema": "pmb-compatibility.apply.v1", "ok": bool(after.get("ok")), "applied": any(item.get("changed") for item in changes), "version": metadata.get("version"), "changes": changes, "before": before, "after": after}


def quick_index_state(pmb_python: Path, pmb_home: Path, workspace: str) -> dict[str, Any]:
    workspace_dir = pmb_home / "workspaces" / workspace
    db_path = workspace_dir / "events.sqlite"
    bm25_path = workspace_dir / "bm25_index.pkl"
    state: dict[str, Any] = {
        "workspace_dir": str(workspace_dir),
        "events_db": str(db_path),
        "bm25_cache": str(bm25_path),
    }
    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            event_count = int(conn.execute("SELECT COUNT(*) FROM events WHERE archived_at IS NULL").fetchone()[0])
            event_unique = int(conn.execute("SELECT COUNT(DISTINCT ulid) FROM events WHERE archived_at IS NULL").fetchone()[0])
        finally:
            conn.close()
        state["events"] = {"count": event_count, "unique_ulids": event_unique, "ok": event_count == event_unique}
    except Exception as exc:
        state["events"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    code = """
import json, pickle, sys
with open(sys.argv[1], 'rb') as handle:
    payload = pickle.load(handle)
ulids = [str(value) for value in payload.get('ulids', [])]
tokens = payload.get('tokens', [])
print(json.dumps({'count': len(ulids), 'unique_ulids': len(set(ulids)), 'token_rows': len(tokens), 'duplicate_ulids': len(ulids) - len(set(ulids)), 'ok': len(ulids) == len(set(ulids)) == len(tokens)}))
"""
    bm25_result = _run_json([str(pmb_python), "-c", code, str(bm25_path)], timeout=60)
    bm25_payload = bm25_result.get("payload") if isinstance(bm25_result.get("payload"), dict) else {}
    state["bm25"] = {
        **bm25_payload,
        "ok": bool(bm25_result.get("ok")) and bool(bm25_payload.get("ok")),
        "error": bm25_result.get("stderr_preview") or bm25_result.get("error"),
    }
    events = state.get("events") if isinstance(state.get("events"), dict) else {}
    bm25 = state.get("bm25") if isinstance(state.get("bm25"), dict) else {}
    state["ok"] = bool(events.get("ok")) and bool(bm25.get("ok")) and events.get("count") == bm25.get("count")
    return state


def lance_index_state(pmb_python: Path, pmb_home: Path, workspace: str) -> dict[str, Any]:
    vector_path = pmb_home / "workspaces" / workspace / "vectors.lance"
    code = """
import json, sys
import lancedb
db = lancedb.connect(sys.argv[1])
response = db.list_tables() if hasattr(db, 'list_tables') else None
names = list(getattr(response, 'tables', response) or []) if response is not None else list(db.table_names() or [])
if 'events' not in names:
    print(json.dumps({'ok': False, 'reason': 'events_table_missing', 'tables': names}))
else:
    table = db.open_table('events')
    arrow = table.to_arrow()
    ulids = [str(value) for value in arrow.column('ulid').to_pylist()]
    print(json.dumps({'ok': len(ulids) == len(set(ulids)), 'count': len(ulids), 'unique_ulids': len(set(ulids)), 'duplicate_ulids': len(ulids) - len(set(ulids))}))
"""
    result = _run_json([str(pmb_python), "-c", code, str(vector_path)], timeout=240)
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    return {
        "ok": bool(result.get("ok")) and bool(payload.get("ok")),
        "path": str(vector_path),
        **payload,
        "error": result.get("stderr_preview") or result.get("error"),
    }


def workspace_env_state(pmb_python: Path, pmb_home: Path) -> dict[str, Any]:
    code = """
import json
from pmb.core.workspace import list_workspaces
rows = [{'id': ws.id, 'name': ws.name, 'pmb_home': str(ws.pmb_home)} for ws in list_workspaces()]
print(json.dumps({'rows': rows}))
"""
    env = os.environ.copy()
    env["PMB_HOME"] = str(pmb_home)
    env["PYTHONIOENCODING"] = "utf-8"
    result = _run_json([str(pmb_python), "-c", code], env=env, timeout=60)
    payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    resolved = {str(Path(str(row.get("pmb_home") or "")).resolve()).casefold() for row in rows if isinstance(row, dict)}
    expected = str(pmb_home.resolve()).casefold()
    return {
        "ok": bool(result.get("ok")) and bool(rows) and resolved == {expected},
        "expected_pmb_home": str(pmb_home),
        "workspace_count": len(rows),
        "rows": rows[:20],
        "error": result.get("stderr_preview") or result.get("error"),
    }


def doctor(
    pmb_python: Path,
    pmb_home: Path,
    workspace: str,
    *,
    full_lance: bool = False,
) -> dict[str, Any]:
    metadata = package_metadata(pmb_python)
    patches = package_patch_state(metadata) if metadata.get("ok") else {"ok": False, "reason": "package_metadata_failed"}
    quick = quick_index_state(pmb_python, pmb_home, workspace)
    workspace_env = workspace_env_state(pmb_python, pmb_home) if metadata.get("ok") else {"ok": False, "reason": "package_metadata_failed"}
    lance = lance_index_state(pmb_python, pmb_home, workspace) if full_lance else {"checked": False, "reason": "lightweight_doctor"}
    counts = []
    for source in (quick.get("events"), quick.get("bm25"), lance):
        if isinstance(source, dict) and source.get("count") is not None:
            counts.append(int(source["count"]))
    counts_consistent = len(set(counts)) <= 1 and len(counts) >= (3 if full_lance else 2)
    issues: list[dict[str, Any]] = []
    if not patches.get("ok"):
        issues.append({"code": "pmb_package_compatibility_missing", "severity": "risk", "detail": patches})
    if not workspace_env.get("ok"):
        issues.append({"code": "pmb_home_workspace_listing_drift", "severity": "risk", "detail": workspace_env})
    if not quick.get("ok") or not counts_consistent:
        issues.append({"code": "pmb_quick_index_mismatch", "severity": "risk", "detail": quick})
    if full_lance and (not lance.get("ok") or not counts_consistent):
        issues.append({"code": "pmb_lance_index_mismatch", "severity": "risk", "detail": lance})
    return {
        "schema": "pmb-compatibility.doctor.v1",
        "ok": not issues,
        "generated_at": now_iso(),
        "full_lance": full_lance,
        "package": metadata,
        "patches": patches,
        "workspace_env": workspace_env,
        "index": {"quick": quick, "lance": lance, "counts": counts, "counts_consistent": counts_consistent},
        "issues": issues,
    }


def repair_plan(pmb_python: Path, pmb_home: Path, workspace: str) -> dict[str, Any]:
    report = doctor(pmb_python, pmb_home, workspace, full_lance=True)
    actions: list[dict[str, Any]] = []
    codes = {str(item.get("code")) for item in report.get("issues", []) if isinstance(item, dict)}
    if {"pmb_package_compatibility_missing", "pmb_home_workspace_listing_drift"} & codes:
        actions.append({
            "id": "apply_pmb_compatibility_patch",
            "command": "python _bridge\\local_pmb_memory.py pmb-compat-apply --apply",
            "guardrails": ["requires exact vulnerable source signature", "stops on PMB package source drift", "uses routed backup before apply"],
        })
    if {"pmb_quick_index_mismatch", "pmb_lance_index_mismatch"} & codes:
        actions.append({
            "id": "reindex_pmb_after_compatibility_patch",
            "command": "python _bridge\\local_pmb_memory.py pmb-compat-apply --apply",
            "guardrails": ["reindex active events only", "restart the governed daemon afterward", "verify SQLite, LanceDB, and BM25 counts"],
        })
    return {
        "schema": "pmb-compatibility.repair_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "doctor_ok": report.get("ok"),
        "actions": actions,
        "doctor": report,
    }
