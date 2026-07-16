"""Persistent trend governance for Windows kernel-pool diagnostics.

Ownership: compact SQLite indexing, trend classification, and bounded monitor scheduling.
Non-goals: changing drivers, services, firewall policy, page-file settings, or rebooting Windows.
State behavior: reads by default; index writes are derived from validated summaries; task install requires confirmation.
Caller context: thinly exposed by ``windows_kernel_pool_diagnostics.py``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "windows_kernel_pool_governance.v1"
DB_NAME = "kernel_pool_trends.sqlite"
LATEST_SUMMARY = "latest-summary.json"
TASK_NAME = "Codex-KernelPool-Governance"
SCHEDULE_CONFIRMATION = "INSTALL-KERNEL-POOL-MONITOR"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
MIB = 1024 * 1024
GIB = 1024 * MIB


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path(output_root: Path) -> Path:
    return output_root / DB_NAME


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS samples (
            id INTEGER PRIMARY KEY,
            captured_at TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL,
            summary_path TEXT NOT NULL,
            evidence_mode TEXT NOT NULL,
            last_boot_time TEXT,
            uptime_hours REAL,
            nvidia_driver_version TEXT,
            pool_nonpaged_bytes INTEGER NOT NULL,
            pool_paged_bytes INTEGER NOT NULL,
            system_nonpaged_bytes INTEGER,
            available_memory_mb INTEGER,
            committed_percent INTEGER,
            quality_status TEXT NOT NULL,
            indexed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS categories (
            sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            PRIMARY KEY (sample_id, category)
        );
        CREATE TABLE IF NOT EXISTS tags (
            sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
            tag TEXT NOT NULL,
            pool_type TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            alloc_free_diff INTEGER NOT NULL,
            mapped TEXT NOT NULL,
            PRIMARY KEY (sample_id, tag, pool_type)
        );
        CREATE INDEX IF NOT EXISTS idx_samples_captured_at ON samples(captured_at DESC);
        CREATE INDEX IF NOT EXISTS idx_categories_name ON categories(category, sample_id);
        """
    )
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(samples)")
    }
    if "last_boot_time" not in columns:
        conn.execute("ALTER TABLE samples ADD COLUMN last_boot_time TEXT")
    return conn


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def index_summary(summary: dict[str, Any], summary_path: Path, output_root: Path) -> dict[str, Any]:
    captured_at = str(summary.get("captured_at") or "").strip()
    if not captured_at:
        raise ValueError("summary captured_at is required")
    totals = summary.get("pool_totals") if isinstance(summary.get("pool_totals"), dict) else {}
    if _int(totals.get("Nonp")) <= 0 or _int(totals.get("Paged")) <= 0:
        raise ValueError("summary pool totals are invalid")
    system = summary.get("system") if isinstance(summary.get("system"), dict) else {}
    quality = summary.get("evidence_quality") if isinstance(summary.get("evidence_quality"), dict) else {}
    categories = summary.get("category_bytes") if isinstance(summary.get("category_bytes"), dict) else {}
    tags = summary.get("top_by_bytes") if isinstance(summary.get("top_by_bytes"), list) else []
    path = db_path(output_root)
    conn = connect(path)
    try:
        with conn:
            old = conn.execute("SELECT id FROM samples WHERE captured_at=?", (captured_at,)).fetchone()
            if old:
                conn.execute("DELETE FROM samples WHERE id=?", (int(old["id"]),))
            cursor = conn.execute(
                """
                INSERT INTO samples (
                    captured_at,label,summary_path,evidence_mode,last_boot_time,uptime_hours,nvidia_driver_version,
                    pool_nonpaged_bytes,pool_paged_bytes,system_nonpaged_bytes,available_memory_mb,
                    committed_percent,quality_status,indexed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    captured_at,
                    str(summary.get("label") or "sample"),
                    str(summary_path),
                    str(summary.get("evidence_mode") or "full"),
                    str(system.get("last_boot_time") or ""),
                    float(system.get("uptime_hours") or 0),
                    str(system.get("nvidia_driver_version") or ""),
                    _int(totals.get("Nonp")),
                    _int(totals.get("Paged")),
                    _int(system.get("pool_nonpaged_bytes")),
                    _int(system.get("available_memory_mb")),
                    _int(system.get("committed_percent")),
                    str(quality.get("status") or "unknown"),
                    now_iso(),
                ),
            )
            sample_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO categories(sample_id,category,bytes) VALUES(?,?,?)",
                [(sample_id, str(name), _int(value)) for name, value in categories.items()],
            )
            conn.executemany(
                "INSERT INTO tags(sample_id,tag,pool_type,bytes,alloc_free_diff,mapped) VALUES(?,?,?,?,?,?)",
                [
                    (
                        sample_id,
                        str(row.get("tag") or ""),
                        str(row.get("type") or ""),
                        _int(row.get("bytes")),
                        _int(row.get("diff")),
                        str(row.get("mapped") or ""),
                    )
                    for row in tags
                    if isinstance(row, dict) and row.get("tag")
                ],
            )
    finally:
        conn.close()
    return {"schema": f"{SCHEMA}.index", "ok": True, "db_path": str(path), "captured_at": captured_at}


def backfill(output_root: Path) -> dict[str, Any]:
    paths = sorted(output_root.glob("*/summary.json"))
    latest = output_root / LATEST_SUMMARY
    if latest.is_file():
        paths.append(latest)
    indexed = 0
    errors: list[dict[str, str]] = []
    for path in paths:
        if path.parent.name.startswith("_"):
            continue
        try:
            summary = json.loads(path.read_text(encoding="utf-8-sig"))
            index_summary(summary, path, output_root)
            indexed += 1
        except Exception as exc:
            errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
    return {
        "schema": f"{SCHEMA}.backfill",
        "ok": not errors,
        "indexed": indexed,
        "errors": errors,
        "db_path": str(db_path(output_root)),
    }


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _category_map(conn: sqlite3.Connection, sample_id: int) -> dict[str, int]:
    return {str(row["category"]): int(row["bytes"]) for row in conn.execute("SELECT category,bytes FROM categories WHERE sample_id=?", (sample_id,))}


def doctor(output_root: Path, *, limit: int = 48) -> dict[str, Any]:
    path = db_path(output_root)
    if not path.is_file():
        backfill(output_root)
    conn = connect(path)
    try:
        total_samples = int(conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0])
        candidate_rows = list(
            conn.execute(
                "SELECT * FROM samples ORDER BY captured_at DESC LIMIT ?",
                (max(2, min(limit, 500)),),
            )
        )
        if not candidate_rows:
            return {"schema": f"{SCHEMA}.doctor", "ok": False, "issues": [{"severity": "risk", "code": "no_indexed_samples"}], "db_path": str(path)}
        newest = candidate_rows[0]
        boot_text = str(newest["last_boot_time"] or "")
        boot_time = _parse_time(boot_text) if boot_text else None
        rows = [
            row
            for row in candidate_rows
            if boot_time is None
            or _parse_time(str(row["captured_at"])) >= boot_time
        ]
        if not rows:
            rows = [newest]
        oldest = rows[-1]
        hours = max((_parse_time(str(newest["captured_at"])) - _parse_time(str(oldest["captured_at"]))).total_seconds() / 3600, 0.0)
        newest_categories = _category_map(conn, int(newest["id"]))
        oldest_categories = _category_map(conn, int(oldest["id"]))
    finally:
        conn.close()

    def rate(current: int, previous: int) -> float | None:
        return ((current - previous) / MIB) / hours if hours >= 0.25 else None

    nonpaged = int(newest["pool_nonpaged_bytes"])
    nonpaged_rate = rate(nonpaged, int(oldest["pool_nonpaged_bytes"]))
    category_rates = {
        key: rate(int(value), int(oldest_categories[key]))
        if key in oldest_categories
        else None
        for key, value in newest_categories.items()
    }
    graphics_bytes = int(newest_categories.get("nvidia", 0)) + int(newest_categories.get("gpu_scheduler", 0))
    graphics_components = [
        category_rates.get("nvidia"),
        category_rates.get("gpu_scheduler"),
    ]
    graphics_rate = (
        sum(float(value) for value in graphics_components if value is not None)
        if any(value is not None for value in graphics_components)
        else None
    )
    firewall_bytes = int(newest_categories.get("firewall_filter", 0))
    firewall_rate = category_rates.get("firewall_filter")
    issues: list[dict[str, Any]] = []
    if nonpaged >= 4 * GIB:
        issues.append({"severity": "risk", "code": "nonpaged_pool_high", "current_mb": round(nonpaged / MIB, 1), "threshold_mb": 4096})
    if nonpaged_rate is not None and nonpaged_rate >= 32:
        issues.append({"severity": "risk", "code": "nonpaged_pool_growing", "rate_mb_per_hour": round(nonpaged_rate, 1), "threshold": 32})
    if graphics_bytes >= 4 * GIB or (graphics_rate is not None and graphics_rate >= 16):
        issues.append({"severity": "risk", "code": "graphics_kernel_pool_pressure", "current_mb": round(graphics_bytes / MIB, 1), "rate_mb_per_hour": None if graphics_rate is None else round(graphics_rate, 1), "drivers": ["nvlddmkm.sys", "dxgkrnl.sys", "dxgmms2.sys"]})
    if firewall_bytes >= 512 * MIB and firewall_rate is not None and firewall_rate >= 16:
        issues.append({"severity": "risk", "code": "firewall_filter_pool_growth", "current_mb": round(firewall_bytes / MIB, 1), "rate_mb_per_hour": round(firewall_rate, 1), "driver": "mpsdrv.sys"})
    if int(newest["available_memory_mb"] or 0) and int(newest["available_memory_mb"]) < 1024:
        issues.append({"severity": "risk", "code": "available_memory_low", "available_memory_mb": int(newest["available_memory_mb"])})
    actions: list[dict[str, Any]] = []
    codes = {str(item.get("code")) for item in issues}
    if "graphics_kernel_pool_pressure" in codes:
        actions.append({"priority": 1, "id": "capture_clean_boot_graphics_baseline", "reason": "separate long-uptime accumulation from current workload before changing drivers"})
        actions.append({"priority": 2, "id": "single_variable_nvidia_driver_ab", "reason": "compare current 610.62 with one approved WHQL target; do not mix HAGS, overlay, virtual-display, and driver changes"})
    if "firewall_filter_pool_growth" in codes:
        actions.append({"priority": 1, "id": "trace_wfp_filter_growth", "reason": "correlate mpsdrv RTLF growth with active WFP providers, VPN/proxy/security clients; do not disable Windows Firewall"})
    return {
        "schema": f"{SCHEMA}.doctor",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "db_path": str(path),
        "sample_count": total_samples,
        "analyzed_sample_count": len(rows),
        "current_boot": boot_text,
        "window_hours": round(hours, 2),
        "newest": dict(newest),
        "rates_mb_per_hour": {"nonpaged": None if nonpaged_rate is None else round(nonpaged_rate, 1), **{key: None if value is None else round(value, 1) for key, value in category_rates.items()}},
        "current_category_mb": {key: round(value / MIB, 1) for key, value in newest_categories.items()},
        "issues": issues,
        "actions": actions,
        "policy": {"success_records": "summary_plus_sqlite", "failure_records": "retain_full_poolmon_evidence", "driver_changes": "single_variable_after_clean_boot_baseline"},
    }


def metrics(output_root: Path) -> dict[str, Any]:
    path = db_path(output_root)
    if not path.is_file():
        backfill(output_root)
    conn = connect(path)
    try:
        sample_count = int(conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0])
        category_count = int(conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0])
        tag_count = int(conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
        newest = conn.execute("SELECT captured_at,label,pool_nonpaged_bytes,available_memory_mb FROM samples ORDER BY captured_at DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    return {
        "schema": f"{SCHEMA}.metrics",
        "ok": True,
        "db_path": str(path),
        "sample_count": sample_count,
        "category_rows": category_count,
        "tag_rows": tag_count,
        "newest": dict(newest) if newest else {},
    }


def _pythonw_path() -> Path:
    executable = Path(sys.executable).resolve()
    candidate = executable.with_name("pythonw.exe")
    return candidate if candidate.is_file() else executable


def schedule_plan(script_path: Path, output_root: Path) -> dict[str, Any]:
    action = subprocess.list2cmdline(
        [
            str(script_path.resolve()),
            "capture",
            "--label",
            "governed-periodic",
            "--summary-only",
            "--top",
            "25",
            "--output-root",
            str(output_root.resolve()),
        ]
    )
    return {
        "schema": f"{SCHEMA}.schedule_plan",
        "ok": True,
        "task_name": TASK_NAME,
        "execute": str(_pythonw_path()),
        "arguments": action,
        "interval_minutes": 30,
        "start_delay_minutes": 5,
        "start_when_available": True,
        "multiple_instances": "IgnoreNew",
        "writes_on_success": [str(db_path(output_root)), str(output_root / LATEST_SUMMARY)],
        "failure_evidence": str(output_root / "<timestamp>-governed-periodic"),
        "confirmation": SCHEDULE_CONFIRMATION,
    }


def schedule_apply(script_path: Path, output_root: Path, *, confirm: str) -> dict[str, Any]:
    plan = schedule_plan(script_path, output_root)
    if confirm != SCHEDULE_CONFIRMATION:
        return {**plan, "applied": False, "reason": "explicit_confirmation_required"}
    execute = Path(plan["execute"])
    if not execute.is_file():
        return {
            **plan,
            "ok": False,
            "applied": False,
            "reason": "python_runtime_missing",
        }
    task_name = TASK_NAME.replace("'", "''")
    exe_text = str(execute).replace("'", "''")
    arguments = str(plan["arguments"]).replace("'", "''")
    script = f"""$ErrorActionPreference='Stop'
+[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
+$action=New-ScheduledTaskAction -Execute '{exe_text}' -Argument '{arguments}'
+$trigger=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
+$settings=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
+Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force | Out-Null
+$task=Get-ScheduledTask -TaskName '{task_name}'
+$info=Get-ScheduledTaskInfo -TaskName '{task_name}'
+[pscustomobject]@{{task_name=$task.TaskName;state=[string]$task.State;next_run=$info.NextRunTime.ToString('o');execute=$task.Actions.Execute;arguments=$task.Actions.Arguments}}|ConvertTo-Json -Compress
+""".replace("\n+", "\n")
    shell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    proc = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90, creationflags=NO_WINDOW)
    try:
        payload = json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    matches = (
        proc.returncode == 0
        and payload.get("task_name") == TASK_NAME
        and str(payload.get("execute") or "").casefold() == str(execute).casefold()
        and str(payload.get("arguments") or "") == str(plan["arguments"])
    )
    return {
        "schema": f"{SCHEMA}.schedule_apply",
        "ok": matches,
        "applied": matches,
        "task": payload,
        "stderr": proc.stderr.strip()[:2000],
        "stdout": proc.stdout.strip()[:2000] if not matches else "",
        "plan": plan,
    }


def validate(output_root: Path) -> dict[str, Any]:
    path = db_path(output_root)
    if not path.is_file():
        backfill(output_root)
    conn = connect(path)
    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        orphan_categories = int(conn.execute("SELECT COUNT(*) FROM categories c LEFT JOIN samples s ON s.id=c.sample_id WHERE s.id IS NULL").fetchone()[0])
        orphan_tags = int(conn.execute("SELECT COUNT(*) FROM tags t LEFT JOIN samples s ON s.id=t.sample_id WHERE s.id IS NULL").fetchone()[0])
    finally:
        conn.close()
    required = {"samples", "categories", "tags"}
    issues = []
    if not required.issubset(tables):
        issues.append({"code": "missing_tables", "missing": sorted(required - tables)})
    if integrity != "ok":
        issues.append({"code": "sqlite_integrity_failed", "detail": integrity})
    if orphan_categories or orphan_tags:
        issues.append({"code": "orphan_rows", "categories": orphan_categories, "tags": orphan_tags})
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": not issues,
        "db_path": str(path),
        "issues": issues,
    }
