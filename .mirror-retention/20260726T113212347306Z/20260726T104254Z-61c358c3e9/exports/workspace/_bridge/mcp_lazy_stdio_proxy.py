#!/usr/bin/env python3
"""Lazy stdio proxy for stateful MCP servers with expensive child runtimes.

Ownership: serve cached MCP initialization/tool catalogs and start one guarded
stateful child only when the current stdio session makes a non-catalog call.
Non-goals: share a stateful child across Codex tasks, alter target permissions,
hide child failures, or replace the existing MCP launch/process guard.
State behavior: cache files are atomic, command-fingerprinted, and bounded by a
maximum age; each proxy owns at most one child for its current stdio session.
Caller context: mcp_profile_launcher for Desktop registration and explicit
warm-cache/eager protocol validation during maintenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_PROTOCOL_VERSION = "2025-11-25"
CACHE_SCHEMA = "mcp_lazy_stdio_proxy.tools_cache.v1"
DEFAULT_CACHE_MAX_AGE_HOURS = 168.0
NO_WINDOW_KW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def _log(profile: str, message: str) -> None:
    sys.stderr.write(f"[mcp-lazy:{profile}] {message.rstrip()}\n")
    sys.stderr.flush()


def _safe_profile_name(profile: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(profile or "unknown")).strip("-.") or "unknown"


def command_fingerprint(command: list[str], child_cwd: str) -> str:
    encoded = json.dumps(
        {"command": [str(item) for item in command], "child_cwd": str(Path(child_cwd).resolve())},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cache_path(cache_dir: Path, profile: str) -> Path:
    return cache_dir / f"{_safe_profile_name(profile)}.json"


def load_cache(
    *,
    cache_dir: Path,
    profile: str,
    command: list[str],
    child_cwd: str,
    max_age_hours: float,
) -> dict[str, Any] | None:
    path = cache_path(cache_dir, profile)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != CACHE_SCHEMA:
        return None
    if payload.get("profile") != profile:
        return None
    if payload.get("command_fingerprint") != command_fingerprint(command, child_cwd):
        return None
    generated_raw = str(payload.get("generated_at") or "")
    try:
        generated_at = datetime.fromisoformat(generated_raw.replace("Z", "+00:00"))
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    age_hours = max(0.0, (datetime.now(timezone.utc) - generated_at).total_seconds() / 3600.0)
    if age_hours > max(0.0, float(max_age_hours)):
        return None
    initialize_result = payload.get("initialize_result")
    tools_result = payload.get("tools_result")
    if not isinstance(initialize_result, dict) or not isinstance(tools_result, dict):
        return None
    if not isinstance(tools_result.get("tools"), list):
        return None
    return payload


def write_cache(
    *,
    cache_dir: Path,
    profile: str,
    command: list[str],
    child_cwd: str,
    initialize_result: dict[str, Any],
    tools_result: dict[str, Any],
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, profile)
    payload = {
        "schema": CACHE_SCHEMA,
        "profile": profile,
        "generated_at": utc_now(),
        "command_fingerprint": command_fingerprint(command, child_cwd),
        "initialize_result": initialize_result,
        "tools_result": tools_result,
        "tool_count": len(tools_result.get("tools") or []),
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return path


class ChildTransport:
    def __init__(self, *, profile: str, command: list[str], child_cwd: str) -> None:
        self.profile = profile
        self.command = command
        self.child_cwd = child_cwd
        self.proc: subprocess.Popen[str] | None = None
        self.stdout_queue: queue.Queue[str | None] = queue.Queue()
        self.startup_backlog: list[dict[str, Any]] = []
        self.relay_thread: threading.Thread | None = None
        self.output_lock = threading.Lock()

    def start(self) -> None:
        if self.proc is not None:
            return
        if not self.command:
            raise RuntimeError("child command is empty")
        self.proc = subprocess.Popen(
            self.command,
            cwd=self.child_cwd,
            env=os.environ.copy(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **NO_WINDOW_KW,
        )
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        _log(self.profile, f"started guarded child pid={self.proc.pid}")

    def _pump_stdout(self) -> None:
        try:
            assert self.proc is not None and self.proc.stdout is not None
            for line in self.proc.stdout:
                self.stdout_queue.put(line)
        finally:
            self.stdout_queue.put(None)

    def _pump_stderr(self) -> None:
        try:
            assert self.proc is not None and self.proc.stderr is not None
            for line in self.proc.stderr:
                _log(self.profile, line)
        except Exception as exc:
            _log(self.profile, f"stderr pump stopped: {exc!r}")

    def send(self, payload: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise BrokenPipeError("child stdin unavailable")
        self.proc.stdin.write(_json_line(payload))
        self.proc.stdin.flush()

    def wait_for_id(self, message_id: str, timeout_seconds: float) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                line = self.stdout_queue.get(timeout=min(0.25, remaining))
            except queue.Empty:
                if self.proc is not None and self.proc.poll() is not None:
                    break
                continue
            if line is None:
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                _log(self.profile, f"child stdout pollution: {line[:500].rstrip()}")
                continue
            if isinstance(payload, dict) and str(payload.get("id")) == str(message_id):
                return payload
            if isinstance(payload, dict):
                self.startup_backlog.append(payload)
        returncode = self.proc.poll() if self.proc is not None else None
        raise TimeoutError(f"child response timeout id={message_id!r} returncode={returncode!r}")

    def initialize(self, params: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        internal_id = "__mcp_lazy_initialize__"
        self.send({"jsonrpc": "2.0", "id": internal_id, "method": "initialize", "params": params})
        response = self.wait_for_id(internal_id, timeout_seconds)
        if isinstance(response.get("error"), dict):
            raise RuntimeError(f"child initialize failed: {response['error']}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("child initialize returned no result object")
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        return result

    def list_tools(self, timeout_seconds: float) -> dict[str, Any]:
        internal_id = "__mcp_lazy_tools_list__"
        self.send({"jsonrpc": "2.0", "id": internal_id, "method": "tools/list", "params": {}})
        response = self.wait_for_id(internal_id, timeout_seconds)
        if isinstance(response.get("error"), dict):
            raise RuntimeError(f"child tools/list failed: {response['error']}")
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            raise RuntimeError("child tools/list returned no tools array")
        return result

    def _write_client(self, payload: dict[str, Any]) -> None:
        with self.output_lock:
            sys.stdout.write(_json_line(payload))
            sys.stdout.flush()

    def start_relay(self) -> None:
        if self.relay_thread is not None:
            return
        for payload in self.startup_backlog:
            self._write_client(payload)
        self.startup_backlog.clear()

        def relay() -> None:
            while True:
                line = self.stdout_queue.get()
                if line is None:
                    return
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    _log(self.profile, f"child stdout pollution: {line[:500].rstrip()}")
                    continue
                if isinstance(payload, dict):
                    self._write_client(payload)

        self.relay_thread = threading.Thread(target=relay, daemon=True)
        self.relay_thread.start()

    def close(self) -> None:
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            _log(self.profile, f"guarded child pid={proc.pid} did not exit after stdin EOF; terminating guard")
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        if self.relay_thread is not None:
            self.relay_thread.join(timeout=2)
        _log(self.profile, f"guarded child pid={proc.pid} exited returncode={proc.returncode}")


def generic_initialize_result(profile: str, params: dict[str, Any]) -> dict[str, Any]:
    requested = str(params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION)
    return {
        "protocolVersion": requested,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": f"codex-lazy-{profile}", "version": "1.0.0"},
    }


def write_client_response(message_id: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(_json_line({"jsonrpc": "2.0", "id": message_id, "result": result}))
    sys.stdout.flush()


def warm_cache(args: argparse.Namespace, command: list[str]) -> int:
    transport = ChildTransport(profile=args.profile, command=command, child_cwd=args.child_cwd)
    try:
        transport.start()
        initialize_params = {
            "protocolVersion": DEFAULT_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "codex-mcp-lazy-cache-warmer", "version": "1.0"},
        }
        initialize_result = transport.initialize(initialize_params, args.child_timeout_seconds)
        tools_result = transport.list_tools(args.child_timeout_seconds)
        path = write_cache(
            cache_dir=Path(args.cache_dir),
            profile=args.profile,
            command=command,
            child_cwd=args.child_cwd,
            initialize_result=initialize_result,
            tools_result=tools_result,
        )
        print(
            json.dumps(
                {"ok": True, "profile": args.profile, "cache_path": str(path), "tool_count": len(tools_result.get("tools") or [])},
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "profile": args.profile, "error": repr(exc)}, ensure_ascii=False))
        return 1
    finally:
        transport.close()


def serve(args: argparse.Namespace, command: list[str]) -> int:
    cache_dir = Path(args.cache_dir)
    cached = load_cache(
        cache_dir=cache_dir,
        profile=args.profile,
        command=command,
        child_cwd=args.child_cwd,
        max_age_hours=args.cache_max_age_hours,
    )
    if cached:
        _log(args.profile, f"catalog cache ready path={cache_path(cache_dir, args.profile)} tools={cached.get('tool_count')}")
    else:
        _log(args.profile, "catalog cache missing, stale, or command-mismatched; tools/list will warm it once")

    initialize_params: dict[str, Any] = {
        "protocolVersion": DEFAULT_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "codex-lazy-proxy-client", "version": "1.0"},
    }
    pending_notifications: list[dict[str, Any]] = []
    transport: ChildTransport | None = None

    def ensure_child() -> tuple[ChildTransport, dict[str, Any]]:
        nonlocal transport
        if transport is not None:
            initialize_result = cached.get("initialize_result") if isinstance(cached, dict) else None
            return transport, initialize_result if isinstance(initialize_result, dict) else generic_initialize_result(args.profile, initialize_params)
        transport = ChildTransport(profile=args.profile, command=command, child_cwd=args.child_cwd)
        transport.start()
        initialize_result = transport.initialize(initialize_params, args.child_timeout_seconds)
        for notification in pending_notifications:
            transport.send(notification)
        pending_notifications.clear()
        return transport, initialize_result

    try:
        for line in sys.stdin:
            text = line.strip()
            if not text:
                continue
            try:
                request = json.loads(text)
            except json.JSONDecodeError as exc:
                _log(args.profile, f"ignored invalid client JSON: {exc}")
                continue
            if not isinstance(request, dict):
                continue
            method = str(request.get("method") or "")
            message_id = request.get("id")
            params = request.get("params") if isinstance(request.get("params"), dict) else {}

            if method == "initialize" and message_id is not None and transport is None:
                initialize_params = params
                result = cached.get("initialize_result") if isinstance(cached, dict) else None
                write_client_response(message_id, result if isinstance(result, dict) else generic_initialize_result(args.profile, params))
                continue
            if method == "notifications/initialized" and transport is None:
                continue
            if method == "ping" and message_id is not None and transport is None:
                write_client_response(message_id, {})
                continue
            if method == "tools/list" and message_id is not None and transport is None:
                tools_result = cached.get("tools_result") if isinstance(cached, dict) else None
                if isinstance(tools_result, dict):
                    write_client_response(message_id, tools_result)
                    continue
                child, initialize_result = ensure_child()
                tools_result = child.list_tools(args.child_timeout_seconds)
                path = write_cache(
                    cache_dir=cache_dir,
                    profile=args.profile,
                    command=command,
                    child_cwd=args.child_cwd,
                    initialize_result=initialize_result,
                    tools_result=tools_result,
                )
                cached = {
                    "initialize_result": initialize_result,
                    "tools_result": tools_result,
                    "tool_count": len(tools_result.get("tools") or []),
                }
                _log(args.profile, f"catalog cache warmed path={path} tools={cached['tool_count']}")
                write_client_response(message_id, tools_result)
                child.start_relay()
                continue
            if message_id is None and transport is None:
                pending_notifications.append(request)
                continue

            child, _ = ensure_child()
            child.start_relay()
            child.send(request)
    except (BrokenPipeError, OSError) as exc:
        _log(args.profile, f"stdio closed: {exc!r}")
    except Exception as exc:
        _log(args.profile, f"proxy failure: {exc!r}")
        return 1
    finally:
        if transport is not None:
            transport.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lazy stdio proxy for stateful MCP child processes")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--child-cwd", required=True)
    parser.add_argument("--cache-max-age-hours", type=float, default=DEFAULT_CACHE_MAX_AGE_HOURS)
    parser.add_argument("--child-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--warm-cache", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("child command is required after --")
    if args.warm_cache:
        return warm_cache(args, command)
    return serve(args, command)


if __name__ == "__main__":
    raise SystemExit(main())
