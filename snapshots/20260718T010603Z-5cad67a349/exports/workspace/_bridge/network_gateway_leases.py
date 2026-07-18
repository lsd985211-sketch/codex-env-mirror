#!/usr/bin/env python3
"""Temporary isolated proxy leases for the Codex network gateway.

Ownership: create, inspect, and stop localhost-only isolated mihomo proxy
leases for one request or a short request batch.
Non-goals: changing the main Clash node, editing Clash config/subscriptions,
changing system proxy/DNS, managing credentials, or running a daemon.
State behavior: writes only lease metadata and isolated config copies under
`_bridge/runtime/codex_network_gateway/leases`; cleanup is explicit or TTL-based.
Caller context: `codex_network_gateway.py` and Hub `network_gateway.lease_*`
tools when a caller needs a temporary proxy endpoint without touching
production network state.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import clash_mihomo_control as mihomo
from shared.process_liveness import process_is_alive as _shared_process_is_alive


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "_bridge" / "runtime" / "codex_network_gateway" / "leases"
SCHEMA_PREFIX = "network_gateway_leases"
SAFE_TARGETS = {"openai", "github", "package", "docs", "browser", "paper", "image", "dataset", "web", "external", "generic"}
DEFAULT_GROUP = "ClashGit.com"
DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 1800
DEFAULT_NODE_HINTS = {
    "openai": "[Japan] 日本-极速 [推荐]",
    "github": "[Japan] 日本-极速 [推荐]",
    "package": "[Japan] 日本-极速 [推荐]",
    "docs": "[Singapore] 新加坡2-极速 [推荐]",
    "browser": "[Japan] 日本-极速 [推荐]",
    "paper": "[Japan] 日本-极速 [推荐]",
    "image": "[Japan] 日本-极速 [推荐]",
    "dataset": "[Japan] 日本-极速 [推荐]",
    "web": "[Japan] 日本-极速 [推荐]",
    "external": "[Japan] 日本-极速 [推荐]",
    "generic": "[Japan] 日本-极速 [推荐]",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def hidden_creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def windows_console_encoding() -> str:
    return "mbcs" if os.name == "nt" else "utf-8"


def lease_path(lease_id: str) -> Path:
    return RUNTIME_DIR / f"{lease_id}.json"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def bounded_ttl(value: int) -> int:
    if value <= 0:
        return DEFAULT_TTL_SECONDS
    return min(value, MAX_TTL_SECONDS)


def wait_for_proxy_check(
    proxy_url: str,
    check_url: str,
    check_method: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Retry the first request until the isolated mixed proxy is ready."""
    deadline = time.time() + max(3, timeout_seconds)
    attempts: list[dict[str, Any]] = []
    while time.time() < deadline:
        remaining = max(1, int(deadline - time.time()))
        check = mihomo.fetch_url_via_proxy(proxy_url, check_url, check_method, min(3, remaining), 1024)
        attempts.append(check)
        if check.get("reachable"):
            check["attempt_count"] = len(attempts)
            return check
        time.sleep(0.35)
    last = attempts[-1] if attempts else {"reachable": False, "error": "proxy_check_not_attempted"}
    last["attempt_count"] = len(attempts)
    return last


def normalize_target(target_kind: str) -> str:
    value = str(target_kind or "generic").strip().lower()
    return value if value in SAFE_TARGETS else "external"


def tasklist_image_name(pid: int) -> str:
    if os.name != "nt":
        return ""
    proc = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        text=True,
        encoding=windows_console_encoding(),
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=hidden_creationflags(),
    )
    text = (proc.stdout or "").strip()
    if not text or "INFO:" in text:
        return ""
    try:
        return next(csv_value for csv_value in json.loads(f"[{text}]") if csv_value).strip()
    except Exception:
        return text.split(",", 1)[0].strip().strip('"')


def process_alive(pid: int) -> bool:
    return _shared_process_is_alive(pid)


def terminate_lease_process(pid: int) -> dict[str, Any]:
    image = tasklist_image_name(pid)
    if os.name == "nt" and image and "mihomo" not in image.lower():
        return {"ok": False, "pid": pid, "reason": "pid_image_not_mihomo", "image": image}
    if not process_alive(pid):
        return {"ok": True, "pid": pid, "already_stopped": True, "image": image}
    if os.name == "nt":
        proc = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            text=True,
            encoding=windows_console_encoding(),
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=hidden_creationflags(),
        )
        return {
            "ok": proc.returncode == 0,
            "pid": pid,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "image": image,
        }
    try:
        os.kill(pid, signal.SIGTERM)
        return {"ok": True, "pid": pid, "image": image}
    except OSError as exc:
        return {"ok": False, "pid": pid, "reason": str(exc), "image": image}


def lease_env(proxy_url: str, target_kind: str) -> dict[str, str]:
    return {
        "HTTP_PROXY": proxy_url,
        "HTTPS_PROXY": proxy_url,
        "ALL_PROXY": proxy_url,
        "NO_PROXY": "localhost,127.0.0.1,::1,.local",
        "NODE_USE_ENV_PROXY": "1",
        "CODEX_NETWORK_CONTEXT": f"codex_gateway_isolated:{target_kind}",
        "CODEX_NETWORK_ROUTE": "isolated_proxy_lease",
    }


def lease_record_files() -> list[Path]:
    if not RUNTIME_DIR.exists():
        return []
    return sorted(RUNTIME_DIR.glob("*.json"))


def lease_status(lease_id: str = "") -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    paths = [lease_path(lease_id)] if lease_id else lease_record_files()
    for path in paths:
        if not path.exists():
            continue
        record = read_json(path)
        pid = int(record.get("pid") or 0)
        expires_at = str(record.get("expires_at") or "")
        expired = False
        if expires_at:
            try:
                expired = datetime.fromisoformat(expires_at) <= now_utc()
            except ValueError:
                expired = False
        record["process_alive"] = process_alive(pid)
        record["expired"] = expired
        record["secret_values_returned"] = False
        records.append(record)
    return {
        "schema": f"{SCHEMA_PREFIX}.status.v1",
        "ok": bool(records) if lease_id else True,
        "generated_at": now_iso(),
        "lease_id": lease_id,
        "count": len(records),
        "leases": records,
    }


def stop_lease(lease_id: str, *, keep_lab_dir: bool = False, reason: str = "manual_stop") -> dict[str, Any]:
    path = lease_path(lease_id)
    if not path.exists():
        return {"schema": f"{SCHEMA_PREFIX}.stop.v1", "ok": False, "lease_id": lease_id, "reason": "lease_not_found"}
    record = read_json(path)
    pid = int(record.get("pid") or 0)
    stop_result = terminate_lease_process(pid)
    lab_dir = Path(str(record.get("lab_dir") or ""))
    removed_lab_dir = False
    if not keep_lab_dir and lab_dir.exists() and RUNTIME_DIR in lab_dir.parents:
        shutil.rmtree(lab_dir, ignore_errors=True)
        removed_lab_dir = not lab_dir.exists()
    archive_dir = RUNTIME_DIR / "stopped"
    archive_dir.mkdir(parents=True, exist_ok=True)
    record.update(
        {
            "stopped_at": now_iso(),
            "stop_reason": reason,
            "stop_result": stop_result,
            "lab_dir_removed": removed_lab_dir,
        }
    )
    archive_path = archive_dir / path.name
    write_json(archive_path, record)
    path.unlink(missing_ok=True)
    return {
        "schema": f"{SCHEMA_PREFIX}.stop.v1",
        "ok": bool(stop_result.get("ok")),
        "generated_at": now_iso(),
        "lease_id": lease_id,
        "stop_result": stop_result,
        "archived": str(archive_path),
        "lab_dir_removed": removed_lab_dir,
        "secret_values_returned": False,
    }


def cleanup_expired(*, keep_lab_dir: bool = False) -> dict[str, Any]:
    stopped: list[dict[str, Any]] = []
    for path in lease_record_files():
        record = read_json(path)
        expires_at = str(record.get("expires_at") or "")
        should_stop = not process_alive(int(record.get("pid") or 0))
        if expires_at:
            try:
                should_stop = should_stop or datetime.fromisoformat(expires_at) <= now_utc()
            except ValueError:
                should_stop = True
        if should_stop:
            stopped.append(stop_lease(str(record.get("lease_id") or path.stem), keep_lab_dir=keep_lab_dir, reason="expired_or_dead"))
    return {
        "schema": f"{SCHEMA_PREFIX}.cleanup.v1",
        "ok": all(item.get("ok") for item in stopped),
        "generated_at": now_iso(),
        "stopped_count": len(stopped),
        "stopped": stopped,
    }


def start_isolated_lease(
    *,
    target_kind: str,
    group: str,
    node: str,
    ttl_seconds: int,
    config_path: str,
    mihomo_path: str,
    check_url: str,
    check_method: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    target = normalize_target(target_kind)
    if target not in SAFE_TARGETS:
        return {"schema": f"{SCHEMA_PREFIX}.start.v1", "ok": False, "reason": "target_kind_not_allowed", "target_kind": target_kind}
    cleanup_expired()
    selected_node = node or DEFAULT_NODE_HINTS.get(target, "")
    if not selected_node:
        return {"schema": f"{SCHEMA_PREFIX}.start.v1", "ok": False, "reason": "missing_node_for_isolated_lease"}
    ttl = bounded_ttl(ttl_seconds)
    lease_id = f"lease-{now_utc().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    lab_dir = RUNTIME_DIR / "active" / lease_id
    lab_dir.mkdir(parents=True, exist_ok=True)
    executable = mihomo.find_mihomo_executable(mihomo_path)
    source_config = Path(config_path) if config_path else mihomo.DEFAULT_VERGE_CONFIG
    mixed_port = mihomo.free_local_port()
    controller_port = mihomo.free_local_port()
    lab_secret = uuid.uuid4().hex + uuid.uuid4().hex
    lab_config = mihomo.build_isolated_config(
        source_config,
        lab_dir,
        mixed_port=mixed_port,
        controller_port=controller_port,
        secret=lab_secret,
    )
    base_url = f"http://127.0.0.1:{controller_port}"
    proc: subprocess.Popen[Any] | None = None
    try:
        proc = subprocess.Popen(
            [str(executable), "-d", str(lab_dir), "-f", str(lab_config)],
            cwd=str(lab_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=hidden_creationflags(),
        )
        mihomo.wait_for_controller(base_url, lab_secret, timeout_seconds=12)
        group_state = mihomo.api_request(base_url, "GET", f"/proxies/{mihomo.urllib.parse.quote(group, safe='')}", timeout=5, secret_value=lab_secret)
        allowed = group_state.get("all") if isinstance(group_state, dict) else None
        if not isinstance(allowed, list) or selected_node not in allowed:
            raise mihomo.ClashControlError(f"node not available in isolated group: {selected_node}")
        mihomo.api_request(base_url, "PUT", f"/proxies/{mihomo.urllib.parse.quote(group, safe='')}", {"name": selected_node}, timeout=5, secret_value=lab_secret)
        selected = mihomo.api_request(base_url, "GET", f"/proxies/{mihomo.urllib.parse.quote(group, safe='')}", timeout=5, secret_value=lab_secret)
        proxy_url = f"http://127.0.0.1:{mixed_port}"
        check = {}
        if check_url:
            check = wait_for_proxy_check(proxy_url, check_url, check_method, timeout_seconds)
            if not check.get("reachable"):
                raise mihomo.ClashControlError(f"lease check failed: {check.get('error') or check.get('status')}")
        expires_at = now_utc() + timedelta(seconds=ttl)
        record = {
            "schema": f"{SCHEMA_PREFIX}.record.v1",
            "lease_id": lease_id,
            "created_at": now_iso(),
            "expires_at": expires_at.isoformat(),
            "ttl_seconds": ttl,
            "pid": proc.pid,
            "target_kind": target,
            "group": group,
            "node": selected_node,
            "selected_after": selected.get("now") if isinstance(selected, dict) else "",
            "proxy_url": proxy_url,
            "controller_url": base_url,
            "mixed_port": mixed_port,
            "controller_port": controller_port,
            "lab_dir": str(lab_dir),
            "config_path": str(lab_config),
            "secret_values_returned": False,
            "safety": {
                "writes_system_proxy": False,
                "writes_dns": False,
                "writes_clash_config": False,
                "changes_codex_conversation_route": False,
                "changes_main_clash_node": False,
            },
        }
        write_json(lease_path(lease_id), record)
        return {
            "schema": f"{SCHEMA_PREFIX}.start.v1",
            "ok": True,
            "generated_at": now_iso(),
            "lease": record,
            "env": lease_env(proxy_url, target),
            "cleanup_command": f"python _bridge\\network_gateway_leases.py stop --lease-id {lease_id}",
            "check": check,
            "secret_values_returned": False,
        }
    except Exception as exc:
        if proc is not None:
            mihomo.terminate_process(proc)
        shutil.rmtree(lab_dir, ignore_errors=True)
        return {
            "schema": f"{SCHEMA_PREFIX}.start.v1",
            "ok": False,
            "generated_at": now_iso(),
            "reason": str(exc)[:500],
            "lease_id": lease_id,
            "lab_dir_removed": not lab_dir.exists(),
            "secret_values_returned": False,
        }


def validate() -> dict[str, Any]:
    status = lease_status()
    cleanup = cleanup_expired()
    return {
        "schema": f"{SCHEMA_PREFIX}.validate.v1",
        "ok": bool(status.get("ok")) and bool(cleanup.get("ok")),
        "generated_at": now_iso(),
        "active_lease_count": status.get("count", 0),
        "cleanup_stopped_count": cleanup.get("stopped_count", 0),
        "runtime_dir": str(RUNTIME_DIR),
        "safe_targets": sorted(SAFE_TARGETS),
        "max_ttl_seconds": MAX_TTL_SECONDS,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex network gateway isolated proxy leases")
    sub = parser.add_subparsers(dest="cmd", required=True)
    start = sub.add_parser("start-isolated")
    start.add_argument("--target-kind", default="external")
    start.add_argument("--group", default=DEFAULT_GROUP)
    start.add_argument("--node", default="")
    start.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    start.add_argument("--config-path", default=str(mihomo.DEFAULT_VERGE_CONFIG))
    start.add_argument("--mihomo-path", default="")
    start.add_argument("--check-url", default="")
    start.add_argument("--check-method", default="HEAD", choices=("GET", "HEAD"))
    start.add_argument("--timeout-seconds", type=int, default=12)
    status = sub.add_parser("status")
    status.add_argument("--lease-id", default="")
    stop = sub.add_parser("stop")
    stop.add_argument("--lease-id", required=True)
    stop.add_argument("--keep-lab-dir", action="store_true")
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--keep-lab-dir", action="store_true")
    sub.add_parser("validate")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.cmd == "start-isolated":
        emit(
            start_isolated_lease(
                target_kind=args.target_kind,
                group=args.group,
                node=args.node,
                ttl_seconds=args.ttl_seconds,
                config_path=args.config_path,
                mihomo_path=args.mihomo_path,
                check_url=args.check_url,
                check_method=args.check_method,
                timeout_seconds=args.timeout_seconds,
            )
        )
    elif args.cmd == "status":
        emit(lease_status(args.lease_id))
    elif args.cmd == "stop":
        emit(stop_lease(args.lease_id, keep_lab_dir=args.keep_lab_dir))
    elif args.cmd == "cleanup":
        emit(cleanup_expired(keep_lab_dir=args.keep_lab_dir))
    elif args.cmd == "validate":
        emit(validate())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
