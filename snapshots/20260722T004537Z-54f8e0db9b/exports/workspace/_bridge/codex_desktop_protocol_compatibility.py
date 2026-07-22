#!/usr/bin/env python3
"""Validate Codex Desktop protocol compatibility without patching the app.

Ownership: inspect the signed Desktop ASAR and isolated app-server behavior for
deprecated-method notification compatibility.
Non-goals: patch ``app.asar``, replace ``thread/rollback``, filter unrelated
notices, restart Desktop, or claim a vendor migration before it ships.
State behavior: read-only package inspection plus isolated temporary app-server
processes whose CODEX_HOME and SQLite state are deleted after each probe.
Caller context: governed Desktop startup preflight and startup owner validation.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import codex_appserver_model_bridge


ROLLBACK_METHOD = "thread/rollback"
DEPRECATION_METHOD = "deprecationNotice"
DEPRECATION_SUMMARY = "thread/rollback is deprecated and will be removed soon"
ASSET_MAX_BYTES = 5 * 1024 * 1024


def _candidate_assets(files: dict[str, dict[str, Any]], prefix: str, suffix: str = ".js") -> list[str]:
    return sorted(
        name
        for name, metadata in files.items()
        if name.startswith(prefix)
        and name.endswith(suffix)
        and int(metadata.get("size") or 0) <= ASSET_MAX_BYTES
    )


def inspect_asar(path: Path) -> dict[str, Any]:
    state: dict[str, Any] = {
        "schema": "codex-desktop-protocol-compatibility.inspect.v1",
        "ok": False,
        "asar_path": str(path),
        "rollback_call_count": 0,
        "migration_complete": False,
        "native_notice_suppression_declared": False,
        "upstream_migration_pending": False,
        "status": "inspection_failed",
        "issues": [],
    }
    try:
        files = codex_appserver_model_bridge.asar_files(path)
        init_assets = _candidate_assets(files, ".vite/build/src-")
        app_assets = _candidate_assets(files, "webview/assets/app-main-")
        initialize_asset = ""
        suppression_declared = False
        for name in init_assets:
            payload = codex_appserver_model_bridge.read_asar_asset(path, name)
            if b"optOutNotificationMethods:" not in payload:
                continue
            initialize_asset = name
            suppression_declared = b"deprecationNotice:!0" in payload
            if suppression_declared:
                break
        rollback_count = 0
        rollback_assets: list[str] = []
        for name in app_assets:
            payload = codex_appserver_model_bridge.read_asar_asset(path, name)
            count = payload.count(b"sendRequest(`thread/rollback`")
            if count:
                rollback_count += count
                rollback_assets.append(name)
        migration_complete = rollback_count == 0
        ok = migration_complete or suppression_declared
        issues: list[dict[str, str]] = []
        if rollback_count and not suppression_declared:
            issues.append({
                "code": "deprecated_method_without_native_notice_optout",
                "action": "update Codex Desktop or use a governed restart after a fixed package is installed",
            })
        return {
            **state,
            "ok": ok,
            "initialize_asset": initialize_asset,
            "rollback_assets": rollback_assets,
            "rollback_call_count": rollback_count,
            "migration_complete": migration_complete,
            "native_notice_suppression_declared": suppression_declared,
            "upstream_migration_pending": not migration_complete,
            "status": (
                "vendor_migration_complete"
                if migration_complete
                else "native_notice_optout_ready"
                if suppression_declared
                else "deprecated_method_without_native_notice_optout"
            ),
            "issues": issues,
        }
    except Exception as exc:
        return {
            **state,
            "error": f"{type(exc).__name__}: {exc}",
            "issues": [{"code": "desktop_asar_inspection_failed", "action": "verify the installed Codex package"}],
        }


def discover_asar() -> Path | None:
    candidates = codex_appserver_model_bridge.candidate_app_asars()
    wsl_windows_apps = Path("/mnt/c/Program Files/WindowsApps")
    if wsl_windows_apps.is_dir():
        candidates.extend(
            path
            for path in sorted(
                wsl_windows_apps.glob("OpenAI.Codex_*/app/resources/app.asar"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            if path not in candidates
        )
    return next(iter(candidates), None)


def discover_app_server_command() -> list[str]:
    configured = os.environ.get("CODEX_CLI_PATH", "").strip()
    candidates: list[Path] = [Path(configured)] if configured else []
    candidates.extend(
        sorted(
            Path("/mnt/c/Users/45543/.codex/bin/wsl").glob("*/codex"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if Path("/mnt/c/Users/45543/.codex/bin/wsl").is_dir()
        else []
    )
    for asar in codex_appserver_model_bridge.candidate_app_asars():
        candidates.append(asar.parent / "codex.exe")
    resolved = shutil.which("codex")
    if resolved:
        candidates.append(Path(resolved))
    for candidate in candidates:
        if candidate.is_file():
            return [str(candidate), "app-server"]
    return []


def _probe_once(command: list[str], *, opt_out: bool, timeout: float) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-protocol-compat-") as temp_dir:
        env = {
            **os.environ,
            "CODEX_HOME": temp_dir,
            "CODEX_SQLITE_HOME": str(Path(temp_dir) / "sqlite"),
            "RUST_LOG": "warn",
        }
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
        messages: queue.Queue[dict[str, Any]] = queue.Queue()

        def read_stdout() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    messages.put(value)

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()
        received: list[dict[str, Any]] = []
        try:
            assert process.stdin is not None
            capabilities: dict[str, Any] = {"experimentalApi": True}
            if opt_out:
                capabilities["optOutNotificationMethods"] = [DEPRECATION_METHOD]
            requests = [
                {
                    "id": "initialize",
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "desktop-protocol-compatibility", "version": "1"},
                        "capabilities": capabilities,
                    },
                },
                {
                    "id": "rollback",
                    "method": ROLLBACK_METHOD,
                    "params": {"threadId": "00000000-0000-0000-0000-000000000000", "numTurns": 1},
                },
            ]
            for request in requests:
                process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + timeout
            rollback_response = False
            while time.monotonic() < deadline:
                try:
                    message = messages.get(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                received.append(message)
                if message.get("id") == "rollback":
                    rollback_response = True
                    break
            notice = any(
                message.get("method") == DEPRECATION_METHOD
                and str((message.get("params") or {}).get("summary") or "") == DEPRECATION_SUMMARY
                for message in received
            )
            initialized = any(message.get("id") == "initialize" and "result" in message for message in received)
            return {
                "ok": initialized and rollback_response,
                "opt_out": opt_out,
                "initialized": initialized,
                "rollback_response": rollback_response,
                "deprecation_notice_received": notice,
                "message_methods": [str(message.get("method") or message.get("id") or "") for message in received],
            }
        finally:
            if process.stdin is not None:
                process.stdin.close()
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            reader.join(timeout=1)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def probe_app_server(command: list[str], *, timeout: float = 4.0) -> dict[str, Any]:
    without_optout = _probe_once(command, opt_out=False, timeout=timeout)
    with_optout = _probe_once(command, opt_out=True, timeout=timeout)
    ok = bool(
        without_optout.get("ok")
        and with_optout.get("ok")
        and without_optout.get("deprecation_notice_received")
        and not with_optout.get("deprecation_notice_received")
    )
    return {
        "schema": "codex-desktop-protocol-compatibility.app-server-probe.v1",
        "ok": ok,
        "command": command,
        "without_optout": without_optout,
        "with_optout": with_optout,
        "status": "optout_honored" if ok else "optout_contract_failed",
    }


def run(command: str, *, asar_path: Path | None, app_server_command: list[str], timeout: float) -> dict[str, Any]:
    resolved_asar = asar_path or discover_asar()
    inspection = inspect_asar(resolved_asar) if resolved_asar else {
        "schema": "codex-desktop-protocol-compatibility.inspect.v1",
        "ok": False,
        "status": "app_asar_not_found",
        "issues": [{"code": "app_asar_not_found", "action": "verify the installed Codex Desktop package"}],
    }
    if command == "inspect":
        return inspection
    resolved_command = app_server_command or discover_app_server_command()
    probe = probe_app_server(resolved_command, timeout=timeout) if resolved_command else {
        "schema": "codex-desktop-protocol-compatibility.app-server-probe.v1",
        "ok": False,
        "status": "app_server_not_found",
    }
    return {
        "schema": "codex-desktop-protocol-compatibility.validate.v1",
        "ok": bool(inspection.get("ok") and probe.get("ok")),
        "inspection": inspection,
        "app_server_probe": probe,
        "acceptance": {
            "signed_package_unchanged": True,
            "native_notice_optout_verified": bool(probe.get("ok")),
            "vendor_protocol_migration_complete": bool(inspection.get("migration_complete")),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex Desktop deprecated-protocol compatibility owner")
    parser.add_argument("command", choices=("inspect", "validate"))
    parser.add_argument("--asar", type=Path)
    parser.add_argument("--codex-exe", type=Path)
    parser.add_argument("--timeout", type=float, default=4.0)
    args = parser.parse_args()
    app_server_command = [str(args.codex_exe), "app-server"] if args.codex_exe else []
    payload = run(args.command, asar_path=args.asar, app_server_command=app_server_command, timeout=args.timeout)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
