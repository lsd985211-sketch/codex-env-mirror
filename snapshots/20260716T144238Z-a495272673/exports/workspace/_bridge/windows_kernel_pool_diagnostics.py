#!/usr/bin/env python3
"""Bounded Microsoft PoolMon evidence capture.

Owns kernel-pool snapshots and compact summaries. It never changes services,
drivers, page-file settings, or system policy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from _bridge import windows_kernel_pool_governance as governance
except (ImportError, ModuleNotFoundError):
    import windows_kernel_pool_governance as governance

try:
    from _bridge import windows_kernel_pool_remediation as remediation
except (ImportError, ModuleNotFoundError):
    import windows_kernel_pool_remediation as remediation

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
POOLMON = Path(r"C:\Program Files (x86)\Windows Kits\10\Tools\10.0.28000.0\x64\poolmon.exe")
POOLTAG = Path(r"C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\triage\pooltag.txt")
OUTPUT_ROOT = Path.home() / "Desktop" / "Codex资源库" / "诊断" / "内核池"
QUALITY_FILE = "quality.json"
QUARANTINE_DIR = "_quarantine"
WPN_HANDLE_THRESHOLD = 50_000
WPN_CONFIRMATION = "RESTART-WPN-HANDLE-LEAK"
POWERSHELL = shutil.which("pwsh.exe") or shutil.which("powershell.exe") or "powershell.exe"
WINDOWS_POWERSHELL = Path(
    os.environ.get("SystemRoot", r"C:\Windows")
) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
ROW_RE = re.compile(
    r"^\s(?P<tag>.{4})\s+(?P<type>Nonp|Paged)\s+(?P<allocs>\d+)\s+"
    r"(?P<frees>\d+)\s+(?P<diff>\d+)\s+(?P<bytes>\d+)\s+"
    r"(?P<per_alloc>\d+)\s*(?P<mapped>.*)$"
)
TOTAL_RE = re.compile(
    r"^Total\s+(?P<type>Nonp|Paged)\s+\d+\s+\(\s*-?\d+\)\s+"
    r"\d+\s+\(\s*-?\d+\)\s+\d+\s+(?P<bytes>\d+)\s+\("
)


def run(command: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW,
        check=False,
    )


def signature(path: Path) -> dict[str, str]:
    escaped = str(path).replace("'", "''")
    script = (
        f"$s=Get-AuthenticodeSignature -LiteralPath '{escaped}';"
        "[pscustomobject]@{status=[string]$s.Status;signer=$s.SignerCertificate.Subject}"
        "|ConvertTo-Json -Compress"
    )
    result = run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script], 30)
    return json.loads(result.stdout) if result.returncode == 0 else {"status": "Unknown", "signer": ""}


def system_snapshot() -> dict[str, Any]:
    script = r'''$m=Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
$r=Get-CimInstance Win32_PerfRawData_PerfOS_Memory
$o=Get-CimInstance Win32_OperatingSystem
$adapters=@(Get-CimInstance Win32_VideoController|ForEach-Object{[pscustomobject]@{name=$_.Name;driver_version=$_.DriverVersion;driver_date=if($_.DriverDate){$_.DriverDate.ToString('o')}else{''};status=$_.Status}})
$nvidia=$adapters|Where-Object{$_.name -match 'NVIDIA'}|Select-Object -First 1
$w=Get-CimInstance Win32_Service -Filter "Name LIKE 'WpnUserService%'"|Select-Object -First 1
$p=if($w -and $w.ProcessId){Get-Process -Id $w.ProcessId -ErrorAction SilentlyContinue}
$processes=@(Get-Process)
$nonp=[int64]$m.PoolNonpagedBytes;if($nonp-le 0){$nonp=[int64]$r.PoolNonpagedBytes}
$paged=[int64]$m.PoolPagedBytes;if($paged-le 0){$paged=[int64]$r.PoolPagedBytes}
$available=[int64]$m.AvailableMBytes;if($available-le 0){$available=[math]::Round($o.FreePhysicalMemory/1024)}
$commit=[int]$m.PercentCommittedBytesInUse;if($commit-le 0-and$o.TotalVirtualMemorySize){$commit=[math]::Round((1-($o.FreeVirtualMemory/$o.TotalVirtualMemorySize))*100)}
[pscustomobject]@{last_boot_time=$o.LastBootUpTime.ToString('o');uptime_hours=[math]::Round(((Get-Date)-$o.LastBootUpTime).TotalHours,2);pool_nonpaged_bytes=$nonp;pool_paged_bytes=$paged;available_memory_mb=$available;committed_percent=$commit;live_processes=$processes.Count;total_process_handles=($processes|Measure-Object HandleCount -Sum).Sum;wpn_service=$w.Name;wpn_pid=$w.ProcessId;wpn_handles=$p.HandleCount;wpn_private_bytes=$p.PrivateMemorySize64;nvidia_driver_version=$nvidia.driver_version;nvidia_driver_date=$nvidia.driver_date;display_adapters=$adapters;virtual_display_present=[bool]($adapters|Where-Object{$_.name -match 'Virtual|GameViewer'})}|ConvertTo-Json -Depth 4 -Compress'''
    result = run(
        [str(WINDOWS_POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", script],
        30,
    )
    return json.loads(result.stdout) if result.returncode == 0 else {"error": result.stderr.strip()}


def snapshot_quality(snapshot: dict[str, Any]) -> dict[str, Any]:
    required = (
        "pool_nonpaged_bytes",
        "pool_paged_bytes",
        "available_memory_mb",
        "committed_percent",
        "live_processes",
    )
    unavailable = [key for key in required if int(snapshot.get(key) or 0) <= 0]
    if snapshot.get("error") or unavailable:
        return {
            "status": "limited",
            "system_snapshot_status": "unavailable",
            "limitations": [
                "system snapshot is incomplete; PoolMon totals and mapped tags remain usable"
            ],
            "unavailable_fields": unavailable,
        }
    return {
        "status": "valid",
        "system_snapshot_status": "available",
        "limitations": [],
        "unavailable_fields": [],
    }


def sample_dir(path: Path, output_root: Path) -> tuple[Path, Path]:
    root = output_root.resolve()
    sample = path.resolve()
    if sample == root or sample.parent != root:
        raise ValueError("sample directory must be a direct child of the evidence root")
    if sample.name.startswith("_"):
        raise ValueError("reserved evidence directory cannot be modified as a sample")
    if not (sample / "summary.json").is_file():
        raise ValueError(f"sample summary is missing: {sample / 'summary.json'}")
    return root, sample


def quality_path(sample: Path) -> Path:
    return sample / QUALITY_FILE


def read_quality(sample: Path, summary: dict[str, Any]) -> dict[str, Any]:
    sidecar = quality_path(sample)
    if sidecar.is_file():
        return json.loads(sidecar.read_text(encoding="utf-8-sig"))
    return summary.get("evidence_quality") or snapshot_quality(summary.get("system", {}))


def annotate(args: argparse.Namespace) -> int:
    _, sample = sample_dir(args.sample_dir, args.output_root)
    payload = {
        "schema": "windows_kernel_pool_diagnostics.quality.v1",
        "status": args.status,
        "scope": args.scope,
        "reason": args.reason,
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    quality_path(sample).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "sample_dir": str(sample), "quality": payload}, ensure_ascii=False))
    return 0


def quarantine(args: argparse.Namespace) -> int:
    root, sample = sample_dir(args.sample_dir, args.output_root)
    quarantine_root = root / QUARANTINE_DIR
    quarantine_root.mkdir(parents=True, exist_ok=True)
    target = quarantine_root / sample.name
    if target.exists():
        raise FileExistsError(f"quarantine target already exists: {target}")
    quality = {
        "schema": "windows_kernel_pool_diagnostics.quality.v1",
        "status": "invalid",
        "scope": "sample",
        "reason": args.reason,
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    quality_path(sample).write_text(
        json.dumps(quality, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    shutil.move(str(sample), str(target))
    print(
        json.dumps(
            {"ok": True, "quarantined_from": str(sample), "quarantined_to": str(target), "quality": quality},
            ensure_ascii=False,
        )
    )
    return 0


def parse(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    totals: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = ROW_RE.match(line)
        if match:
            item: dict[str, Any] = match.groupdict()
            for key in ("allocs", "frees", "diff", "bytes", "per_alloc"):
                item[key] = int(item[key])
            item["tag"] = item["tag"].strip()
            item["mapped"] = item["mapped"].strip()
            rows.append(item)
            continue
        total = TOTAL_RE.match(line)
        if total:
            totals[total.group("type")] = int(total.group("bytes"))
    if not rows:
        raise RuntimeError(f"No PoolMon rows found in {path}")
    return rows, totals


def category_bytes(rows: list[dict[str, Any]]) -> dict[str, int]:
    def total(predicate: Any) -> int:
        return sum(
            row["bytes"]
            for row in rows
            if predicate(row["tag"].lower(), row["mapped"].lower())
        )

    return {
        "nvidia": total(
            lambda tag, mapped: tag.startswith("nv")
            or "nvidia" in mapped
            or "nvlddmkm" in mapped
        ),
        "gpu_scheduler": total(
            lambda tag, mapped: tag.startswith("vi")
            or "dxgmms2" in mapped
            or "dxgkrnl" in mapped
        ),
        "firewall_filter": total(
            lambda tag, mapped: tag == "rtlf" or "mpsdrv" in mapped
        ),
        "etw": total(lambda tag, mapped: tag.startswith("etw") or "etw" in mapped),
        "notifications": total(
            lambda tag, mapped: tag.startswith("wpn") or "notification" in mapped
        ),
        "storage_rst": total(
            lambda tag, mapped: tag == "ismc"
            or "iastor" in mapped
            or "rapid storage" in mapped
        ),
        "security_objects": total(
            lambda tag, mapped: tag in {"toke", "seat"}
            or "token objects" in mapped
            or "security attributes" in mapped
        ),
    }


def baseline_delta(
    values: dict[str, int], baseline: dict[str, Any]
) -> dict[str, int | None]:
    return {
        key: value - int(baseline[key]) if key in baseline else None
        for key, value in values.items()
    }


def wpn_decision(categories: dict[str, int], system: dict[str, Any]) -> dict[str, Any]:
    notification_dominant = categories.get("notifications", 0) > max(
        64 * 1024 * 1024, categories.get("nvidia", 0) // 2
    )
    handles = int(system.get("wpn_handles") or 0)
    handle_leak = handles >= WPN_HANDLE_THRESHOLD
    reasons = []
    if notification_dominant:
        reasons.append("notification_pool_tags_are_dominant")
    if handle_leak:
        reasons.append("wpn_process_handle_count_exceeds_threshold")
    return {
        "wpn_restart_supported": notification_dominant or handle_leak,
        "notification_pool_dominant": notification_dominant,
        "wpn_handle_leak_suspected": handle_leak,
        "wpn_handles": handles,
        "wpn_handle_threshold": WPN_HANDLE_THRESHOLD,
        "reasons": reasons,
        "rule": (
            "Restart WpnUserService only when notification pool tags are dominant "
            "or its process handle count exceeds the governed threshold."
        ),
    }


def capture(args: argparse.Namespace) -> int:
    poolmon = args.poolmon.resolve()
    pooltag = args.pooltag.resolve()
    if not poolmon.is_file() or not pooltag.is_file():
        raise FileNotFoundError(f"PoolMon requirements missing: {poolmon}, {pooltag}")
    signed = signature(poolmon)
    if signed.get("status") != "Valid" or "Microsoft Corporation" not in signed.get(
        "signer", ""
    ):
        raise RuntimeError(f"Untrusted PoolMon binary: {signed}")

    label = re.sub(r"[^A-Za-z0-9._-]+", "-", args.label).strip("-.") or "sample"
    evidence = args.output_root.resolve() / f"{datetime.now():%Y%m%d-%H%M%S}-{label}"
    evidence.mkdir(parents=True, exist_ok=False)
    byte_log = evidence / "pool-all-bytes-mapped.log"
    diff_log = evidence / "pool-all-diff-mapped.log"
    try:
        for sort, output in (("/b", byte_log), ("/d", diff_log)):
            result = run(
                [str(poolmon), "/g", str(pooltag), sort, "/e", "/n", str(output)]
            )
            if result.returncode != 0 or not output.is_file():
                reason = result.stderr.strip() or result.stdout.strip() or "snapshot_missing"
                raise RuntimeError(f"PoolMon failed ({result.returncode}): {reason}")
    except Exception as exc:
        (evidence / "failure.json").write_text(
            json.dumps(
                {
                    "schema": "windows_kernel_pool_diagnostics.failure.v1",
                    "ok": False,
                    "captured_at": datetime.now().astimezone().isoformat(),
                    "label": label,
                    "error_class": type(exc).__name__,
                    "reason": str(exc),
                    "raw_evidence_retained": True,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        raise

    rows, totals = parse(byte_log)
    diff_rows, _ = parse(diff_log)
    categories = category_bytes(rows)
    baseline = (
        json.loads(args.baseline.read_text(encoding="utf-8-sig"))
        if args.baseline
        else {}
    )
    base_categories = baseline.get("category_bytes", {})
    base_totals = baseline.get("pool_totals", {})
    system = system_snapshot()
    summary = {
        "schema": "windows_kernel_pool_diagnostics.sample.v1",
        "ok": True,
        "captured_at": datetime.now().astimezone().isoformat(),
        "label": label,
        "evidence_mode": "summary_only" if args.summary_only else "full",
        "raw_evidence_retained": not args.summary_only,
        "evidence_dir": str(evidence),
        "poolmon": {
            "path": str(poolmon),
            "pooltag_path": str(pooltag),
            "authenticode": signed,
        },
        "system": system,
        "evidence_quality": snapshot_quality(system),
        "pool_totals": totals,
        "pool_total_delta_from_baseline": baseline_delta(totals, base_totals)
        if baseline
        else {},
        "category_bytes": categories,
        "category_delta_from_baseline": baseline_delta(categories, base_categories)
        if baseline
        else {},
        "top_by_bytes": sorted(
            rows, key=lambda item: item["bytes"], reverse=True
        )[: args.top],
        "top_by_alloc_free_diff": sorted(
            diff_rows, key=lambda item: item["diff"], reverse=True
        )[: args.top],
        "decision_guard": wpn_decision(categories, system),
    }
    summary_path = evidence / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    indexed_path = summary_path
    if args.summary_only:
        summary["evidence_dir"] = ""
        latest_path = args.output_root.resolve() / governance.LATEST_SUMMARY
        temporary = latest_path.with_name(f".{latest_path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, latest_path)
        indexed_path = latest_path
    index_result = governance.index_summary(summary, indexed_path, args.output_root.resolve())
    if args.summary_only:
        shutil.rmtree(evidence)
    print(
        json.dumps(
            {
                "ok": True,
                "summary_path": str(indexed_path),
                "evidence_mode": summary["evidence_mode"],
                "raw_evidence_retained": summary["raw_evidence_retained"],
                "index": index_result,
                "pool_totals": totals,
                "category_bytes": categories,
                "wpn_restart_supported": summary["decision_guard"][
                    "wpn_restart_supported"
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


def wpn_plan(args: argparse.Namespace) -> int:
    system = system_snapshot()
    decision = wpn_decision({}, system)
    print(
        json.dumps(
            {
                "schema": "windows_kernel_pool_diagnostics.wpn_plan.v1",
                "ok": True,
                "system": system,
                "decision": decision,
                "apply_available": decision["wpn_handle_leak_suspected"],
                "apply_command": (
                    f"python {Path(__file__).name} wpn-recover --confirm {WPN_CONFIRMATION}"
                    if decision["wpn_handle_leak_suspected"]
                    else ""
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


def wpn_recover(args: argparse.Namespace) -> int:
    before = system_snapshot()
    decision = wpn_decision({}, before)
    if not decision["wpn_handle_leak_suspected"]:
        print(
            json.dumps(
                {
                    "schema": "windows_kernel_pool_diagnostics.wpn_recover.v1",
                    "ok": True,
                    "applied": False,
                    "reason": "handle_count_below_threshold",
                    "before": before,
                    "decision": decision,
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.confirm != WPN_CONFIRMATION:
        raise ValueError(f"explicit confirmation required: {WPN_CONFIRMATION}")
    service = str(before.get("wpn_service") or "")
    if not service:
        raise RuntimeError("WpnUserService instance not found")
    escaped = service.replace("'", "''")
    script = f'''$name='{escaped}'
$errorText=$null
try {{ Restart-Service -Name $name -Force -ErrorAction Stop; Start-Sleep -Seconds 15 }}
catch {{ $errorText=$_.Exception.Message }}
finally {{ $state=Get-Service -Name $name -ErrorAction SilentlyContinue; if($state -and $state.Status -ne 'Running'){{Start-Service -Name $name -ErrorAction SilentlyContinue;Start-Sleep -Seconds 8}} }}
$service=Get-CimInstance Win32_Service -Filter "Name='$name'"
[pscustomobject]@{{name=$name;state=$service.State;process_id=$service.ProcessId;error=$errorText}}|ConvertTo-Json -Compress'''
    result = run(
        [str(WINDOWS_POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", script],
        45,
    )
    after = system_snapshot()
    service_result = (
        json.loads(result.stdout)
        if result.returncode == 0 and result.stdout.strip()
        else {"state": "Unknown", "error": result.stderr.strip()}
    )
    ok = service_result.get("state") == "Running"
    print(
        json.dumps(
            {
                "schema": "windows_kernel_pool_diagnostics.wpn_recover.v1",
                "ok": ok,
                "applied": True,
                "before": before,
                "after": after,
                "service_result": service_result,
                "handle_delta": int(after.get("wpn_handles") or 0)
                - int(before.get("wpn_handles") or 0),
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


def status(args: argparse.Namespace) -> int:
    root = args.output_root.resolve()
    paths = sorted(
        root.glob("*/summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    samples = []
    for path in paths[: args.limit]:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        quality = read_quality(path.parent, data)
        samples.append(
            {
                "path": str(path),
                "captured_at": data.get("captured_at"),
                "label": data.get("label"),
                "evidence_quality": quality,
                "system": data.get("system", {}),
                "pool_totals": data.get("pool_totals", {}),
                "category_bytes": data.get("category_bytes", {}),
                "category_delta_from_baseline": data.get(
                    "category_delta_from_baseline", {}
                ),
            }
        )
    print(
        json.dumps(
            {
                "schema": "windows_kernel_pool_diagnostics.status.v1",
                "ok": True,
                "sample_count": len(paths),
                "quarantine_count": len(list((root / QUARANTINE_DIR).glob("*/summary.json"))),
                "returned_count": len(samples),
                "truncated": len(paths) > len(samples),
                "samples": samples,
            },
            ensure_ascii=False,
        )
    )
    return 0


def governance_backfill(args: argparse.Namespace) -> int:
    result = governance.backfill(args.output_root.resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def governance_doctor(args: argparse.Namespace) -> int:
    result = governance.doctor(args.output_root.resolve(), limit=args.limit)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def governance_metrics(args: argparse.Namespace) -> int:
    result = governance.metrics(args.output_root.resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def governance_schedule_plan(args: argparse.Namespace) -> int:
    result = governance.schedule_plan(Path(__file__), args.output_root.resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0


def governance_schedule_apply(args: argparse.Namespace) -> int:
    result = governance.schedule_apply(
        Path(__file__), args.output_root.resolve(), confirm=args.confirm
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def governance_validate(args: argparse.Namespace) -> int:
    result = governance.validate(args.output_root.resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def wfp_repair_plan(args: argparse.Namespace) -> int:
    result = remediation.wfp_plan(example_limit=args.limit)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def wfp_repair_apply(args: argparse.Namespace) -> int:
    result = remediation.wfp_apply(
        args.output_root.resolve(),
        Path(__file__),
        confirm=args.confirm,
        plan_id=args.plan_id,
        limit=args.limit,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def wfp_repair_rollback(args: argparse.Namespace) -> int:
    result = remediation.wfp_rollback(
        args.output_root.resolve(),
        Path(__file__),
        backup=args.backup,
        confirm=args.confirm,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    take = commands.add_parser("capture", help="Capture one bounded PoolMon sample")
    take.add_argument("--label", required=True)
    take.add_argument("--poolmon", type=Path, default=POOLMON)
    take.add_argument("--pooltag", type=Path, default=POOLTAG)
    take.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    take.add_argument("--baseline", type=Path)
    take.add_argument("--top", type=int, default=25)
    take.add_argument(
        "--summary-only",
        action="store_true",
        help="Retain compact latest summary and SQLite trend rows; keep raw logs only on failure",
    )
    take.set_defaults(func=capture)

    report = commands.add_parser("status", help="Read compact sample summaries")
    report.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    report.add_argument("--limit", type=int, default=10)
    report.set_defaults(func=status)

    note = commands.add_parser("annotate", help="Attach a machine-readable evidence quality note")
    note.add_argument("--sample-dir", type=Path, required=True)
    note.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    note.add_argument("--status", choices=("valid", "limited", "invalid"), required=True)
    note.add_argument("--scope", default="sample")
    note.add_argument("--reason", required=True)
    note.set_defaults(func=annotate)

    isolate = commands.add_parser("quarantine", help="Move an invalid direct-child sample into bounded quarantine")
    isolate.add_argument("--sample-dir", type=Path, required=True)
    isolate.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    isolate.add_argument("--reason", required=True)
    isolate.set_defaults(func=quarantine)

    plan_wpn = commands.add_parser("wpn-plan", help="Inspect the bounded WPN handle-leak recovery condition")
    plan_wpn.set_defaults(func=wpn_plan)

    recover_wpn = commands.add_parser("wpn-recover", help="Restart WPN only after the handle-leak guard is met")
    recover_wpn.add_argument("--confirm", default="")
    recover_wpn.set_defaults(func=wpn_recover)

    rebuild = commands.add_parser("index-rebuild", help="Rebuild the kernel-pool SQLite trend index")
    rebuild.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    rebuild.set_defaults(func=governance_backfill)

    doctor = commands.add_parser("doctor", help="Classify bounded kernel-pool trends from SQLite")
    doctor.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    doctor.add_argument("--limit", type=int, default=48)
    doctor.set_defaults(func=governance_doctor)

    metrics = commands.add_parser("metrics", help="Read compact kernel-pool index metrics")
    metrics.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    metrics.set_defaults(func=governance_metrics)

    schedule = commands.add_parser("schedule-plan", help="Show the bounded periodic monitor plan")
    schedule.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    schedule.set_defaults(func=governance_schedule_plan)

    install = commands.add_parser("schedule-apply", help="Install or update the bounded periodic monitor")
    install.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    install.add_argument("--confirm", default="")
    install.set_defaults(func=governance_schedule_apply)

    validate = commands.add_parser("validate", help="Validate the kernel-pool trend index")
    validate.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    validate.set_defaults(func=governance_validate)

    wfp_plan = commands.add_parser("wfp-plan", help="Plan a bounded stale local-firewall-rule cleanup")
    wfp_plan.add_argument("--limit", type=int, default=30)
    wfp_plan.set_defaults(func=wfp_repair_plan)

    wfp_apply = commands.add_parser("wfp-apply", help="Apply one fingerprint-locked WFP cleanup batch")
    wfp_apply.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    wfp_apply.add_argument("--plan-id", required=True)
    wfp_apply.add_argument("--limit", type=int, default=25)
    wfp_apply.add_argument("--confirm", default="")
    wfp_apply.set_defaults(func=wfp_repair_apply)

    wfp_rollback = commands.add_parser("wfp-rollback", help="Restore a governed firewall policy export")
    wfp_rollback.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    wfp_rollback.add_argument("--backup", type=Path, required=True)
    wfp_rollback.add_argument("--confirm", default="")
    wfp_rollback.set_defaults(func=wfp_repair_rollback)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        args.top = max(5, min(getattr(args, "top", 25), 100))
        return args.func(args)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema": "windows_kernel_pool_diagnostics.error.v1",
                    "ok": False,
                    "error_class": type(exc).__name__,
                    "reason": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
