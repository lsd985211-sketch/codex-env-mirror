"""Evidence-gated remediation for Windows kernel-pool pressure.

Ownership: reversible stale local-firewall-rule cleanup and post-action sampling.
Non-goals: disabling Windows Firewall, changing WFP drivers, or changing display drivers.
State behavior: read-only plans; apply and rollback require exact confirmation.
Caller context: exposed by windows_kernel_pool_diagnostics.py as a thin facade.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = "windows_kernel_pool_remediation.v1"
WFP_CONFIRMATION = "REMOVE-STALE-LOCAL-FIREWALL-RULES"
WFP_ROLLBACK_CONFIRMATION = "RESTORE-FIREWALL-POLICY"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
WINDOWS_POWERSHELL = Path(
    os.environ.get("SystemRoot", r"C:\Windows")
) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
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


def _powershell_json(script: str, *, timeout: int = 120) -> Any:
    wrapped = (
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
        + script
    )
    result = _run(
        [
            str(WINDOWS_POWERSHELL),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            wrapped,
        ],
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout) if result.stdout.strip() else None


def wfp_inventory() -> dict[str, Any]:
    script = r'''$rules=@{}
Get-NetFirewallRule -PolicyStore ActiveStore -ErrorAction Stop | ForEach-Object {$rules[$_.Name]=$_}
$apps=@(Get-NetFirewallApplicationFilter -PolicyStore ActiveStore -ErrorAction Stop)
$candidates=@()
foreach($app in $apps){
  $program=[Environment]::ExpandEnvironmentVariables([string]$app.Program)
  $rule=$rules[[string]$app.InstanceID]
  if(-not $rule -or -not $program -or $program -in @('Any','System')){continue}
  $absolute=$program -match '^[A-Za-z]:[\\]'
  $systemPath=$program -match '(?i)[\\]WindowsApps[\\]|^[A-Za-z]:[\\]Windows[\\]'
  $local=([int]$rule.PolicyStoreSourceType) -eq 1
  if($absolute -and $local -and -not $systemPath -and -not $app.Package -and -not (Test-Path -LiteralPath $program)){
    $class='manual_review'
    if($program -match '(?i)^[A-Za-z]:[\\]Users[\\]([^\\]+)[\\]'){
      $profileRoot=Join-Path ([System.IO.Path]::GetPathRoot($program)) 'Users'
      $profile=Join-Path $profileRoot $matches[1]
      if(-not(Test-Path -LiteralPath $profile)){$class='orphaned_user_profile'}
      elseif($program -match '(?i)[\\]Downloads[\\]'){$class='missing_download_artifact'}
      elseif($program -match '(?i)[\\]AppData[\\]Local[\\]Temp[\\]'){$class='missing_temp_artifact'}
    }
    $candidates += [pscustomobject]@{name=[string]$rule.Name;display_name=[string]$rule.DisplayName;program=$program;enabled=[string]$rule.Enabled;direction=[string]$rule.Direction;action=[string]$rule.Action;owner=[string]$rule.Owner;safety_class=$class;eligible=$class-ne'manual_review'}
  }
}
$bindings=@(Get-NetAdapterBinding -AllBindings -ErrorAction SilentlyContinue | Where-Object {$_.Enabled -and $_.ComponentID -notmatch '^ms_'} | Select-Object Name,DisplayName,ComponentID,Enabled)
$drivers=@(Get-CimInstance Win32_SystemDriver | Where-Object {$_.State -eq 'Running' -and ($_.Name -match 'XunYou|rtf64|wfp|filter' -or $_.DisplayName -match 'filter|WFP')} | Select-Object Name,DisplayName,PathName,StartMode,State)
$processes=@(Get-Process -ErrorAction SilentlyContinue | Where-Object {$_.ProcessName -match 'clash|mihomo|vpn|wireguard|openvpn|tailscale|zerotier|gameviewer'} | Select-Object ProcessName,Id,Path)
[pscustomobject]@{total_rules=$rules.Count;application_filters=$apps.Count;stale_candidates=$candidates;third_party_bindings=$bindings;relevant_drivers=$drivers;relevant_processes=$processes} | ConvertTo-Json -Depth 6 -Compress'''
    payload = _powershell_json(script)
    candidates = payload.get("stale_candidates") or []
    if isinstance(candidates, dict):
        candidates = [candidates]
    payload["stale_candidates"] = candidates
    payload["eligible_candidates"] = [
        item for item in candidates if bool(item.get("eligible"))
    ]
    return payload


def _plan_id(candidates: list[dict[str, Any]]) -> str:
    stable = sorted(
        ({"name": item.get("name"), "program": item.get("program")} for item in candidates),
        key=lambda item: (str(item["program"]), str(item["name"])),
    )
    encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def wfp_plan(*, example_limit: int = 30) -> dict[str, Any]:
    inventory = wfp_inventory()
    candidates = inventory.pop("stale_candidates")
    eligible = inventory.pop("eligible_candidates")
    review = [item for item in candidates if not bool(item.get("eligible"))]
    return {
        "schema": f"{SCHEMA}.wfp_plan",
        "ok": True,
        "plan_id": _plan_id(eligible),
        "stale_candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "manual_review_count": len(review),
        "eligible_examples": eligible[: max(1, min(example_limit, 100))],
        "manual_review_examples": review[: max(1, min(example_limit, 100))],
        "inventory": inventory,
        "batch_limit": 25,
        "scope": {
            "included": "local missing-path rules tied to a removed user profile, Downloads artifact, or temp artifact",
            "excluded": [
                "Windows and WindowsApps paths",
                "package and group-policy rules",
                "active paths and missing application-version paths",
                "WFP drivers and Windows Firewall service",
            ],
        },
        "confirmation": WFP_CONFIRMATION,
        "rollback_confirmation": WFP_ROLLBACK_CONFIRMATION,
    }


def _repair_dir(output_root: Path) -> Path:
    path = output_root / "repairs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _post_capture(diagnostics_script: Path, output_root: Path, label: str) -> dict[str, Any]:
    result = _run(
        [
            sys.executable,
            str(diagnostics_script),
            "capture",
            "--label",
            label,
            "--summary-only",
            "--top",
            "25",
            "--output-root",
            str(output_root),
        ],
        timeout=180,
    )
    try:
        payload = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    return {
        "ok": result.returncode == 0 and bool(payload.get("ok")),
        "result": payload,
        "stderr": result.stderr.strip()[:2000],
    }


def wfp_apply(
    output_root: Path,
    diagnostics_script: Path,
    *,
    confirm: str,
    plan_id: str,
    limit: int = 25,
) -> dict[str, Any]:
    inventory = wfp_inventory()
    candidates = inventory["eligible_candidates"]
    current_plan_id = _plan_id(candidates)
    if confirm != WFP_CONFIRMATION or plan_id != current_plan_id:
        return {
            "schema": f"{SCHEMA}.wfp_apply",
            "ok": False,
            "applied": False,
            "reason": "exact_confirmation_and_current_plan_id_required",
            "confirmation": WFP_CONFIRMATION,
            "plan_id": current_plan_id,
            "eligible_count": len(candidates),
        }
    batch = candidates[: max(1, min(limit, 25))]
    if not batch:
        return {
            "schema": f"{SCHEMA}.wfp_apply",
            "ok": True,
            "applied": False,
            "reason": "no_candidates",
        }
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    repair_dir = _repair_dir(output_root)
    policy_backup = repair_dir / f"firewall-policy-{stamp}.wfw"
    manifest = repair_dir / f"firewall-stale-rule-repair-{stamp}.json"
    exported = _run(
        ["netsh.exe", "advfirewall", "export", str(policy_backup)], timeout=120
    )
    if exported.returncode != 0 or not policy_backup.is_file():
        return {
            "schema": f"{SCHEMA}.wfp_apply",
            "ok": False,
            "applied": False,
            "reason": "firewall_policy_export_failed",
            "stderr": exported.stderr.strip()[:2000],
        }
    record = {
        "schema": f"{SCHEMA}.wfp_repair_record",
        "created_at": datetime.now().astimezone().isoformat(),
        "plan_id": current_plan_id,
        "policy_backup": str(policy_backup),
        "batch": batch,
        "status": "planned",
    }
    manifest.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    escaped = str(manifest).replace("'", "''")
    removal = _powershell_json(
        f"$record=Get-Content -LiteralPath '{escaped}' -Raw -Encoding UTF8 | ConvertFrom-Json;"
        "$removed=@();$failed=@();foreach($item in $record.batch){try{"
        "Remove-NetFirewallRule -Name ([string]$item.name) -ErrorAction Stop;"
        "$removed+=[string]$item.name}catch{$failed+=[pscustomobject]@{name=[string]$item.name;error=$_.Exception.Message}}};"
        "[pscustomobject]@{removed=$removed;failed=$failed}|ConvertTo-Json -Depth 4 -Compress",
        timeout=180,
    )
    removed = removal.get("removed") or []
    failed = removal.get("failed") or []
    if isinstance(removed, str):
        removed = [removed]
    if isinstance(failed, dict):
        failed = [failed]
    after = wfp_inventory()
    capture = _post_capture(
        diagnostics_script, output_root, "after-wfp-stale-rule-repair"
    )
    record.update(
        {
            "status": "completed" if not failed else "partial",
            "removed": removed,
            "failed": failed,
            "remaining_eligible": len(after["eligible_candidates"]),
            "remaining_manual_review": len(after["stale_candidates"])
            - len(after["eligible_candidates"]),
            "post_capture": capture,
        }
    )
    manifest.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "schema": f"{SCHEMA}.wfp_apply",
        "ok": not failed and capture["ok"],
        "applied": bool(removed),
        "removed_count": len(removed),
        "failed": failed,
        "remaining_eligible": len(after["eligible_candidates"]),
        "remaining_manual_review": len(after["stale_candidates"])
        - len(after["eligible_candidates"]),
        "policy_backup": str(policy_backup),
        "manifest": str(manifest),
        "post_capture": capture,
    }


def wfp_rollback(
    output_root: Path,
    diagnostics_script: Path,
    *,
    backup: Path,
    confirm: str,
) -> dict[str, Any]:
    if confirm != WFP_ROLLBACK_CONFIRMATION:
        return {
            "schema": f"{SCHEMA}.wfp_rollback",
            "ok": False,
            "applied": False,
            "reason": "explicit_confirmation_required",
            "confirmation": WFP_ROLLBACK_CONFIRMATION,
        }
    resolved = backup.resolve()
    repair_root = _repair_dir(output_root).resolve()
    if repair_root not in resolved.parents or not resolved.is_file():
        raise ValueError("backup must be an existing governed firewall policy export")
    restored = _run(
        ["netsh.exe", "advfirewall", "import", str(resolved)], timeout=180
    )
    capture = _post_capture(
        diagnostics_script, output_root, "after-wfp-policy-rollback"
    )
    return {
        "schema": f"{SCHEMA}.wfp_rollback",
        "ok": restored.returncode == 0 and capture["ok"],
        "applied": restored.returncode == 0,
        "backup": str(resolved),
        "stderr": restored.stderr.strip()[:2000],
        "post_capture": capture,
    }
