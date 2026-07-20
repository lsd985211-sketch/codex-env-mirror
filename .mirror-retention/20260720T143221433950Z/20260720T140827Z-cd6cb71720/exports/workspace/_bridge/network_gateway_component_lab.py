#!/usr/bin/env python3
"""Lab-only component experiments for the Codex network gateway.

Ownership: validates external gateway component fit for Codex network routing.
Non-goals: production route integration, system proxy/DNS edits, Clash config
mutation, subscription management, or persistent background service creation.
State behavior: read-only by default; experiment commands write only under
`_bridge/runtime/network_gateway_lab` and must clean up processes they start.
Caller context: Codex network gateway design, resource-layer routing trials,
and user-approved local component experiments.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
RUNTIME_DIR = BRIDGE / "runtime" / "network_gateway_lab"
SCHEMA_PREFIX = "network_gateway_component_lab"
DEFAULT_UPSTREAM_PROXY = "http://127.0.0.1:7897"
DEFAULT_TEST_URL = "https://api.github.com/"
PREFERRED_NODE_EXE = Path("C:/Program Files/nodejs/node.exe")
PREFERRED_NPM_CMD = Path("C:/Program Files/nodejs/npm.cmd")


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def hidden_creationflags() -> int:
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_capture(argv: list[str], *, cwd: Path | None = None, timeout: int = 20) -> CommandResult:
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            creationflags=hidden_creationflags(),
        )
        return CommandResult(proc.returncode == 0, argv, proc.returncode, proc.stdout.strip(), proc.stderr.strip())
    except Exception as exc:
        return CommandResult(False, argv, -1, "", str(exc))


def command_path(preferred: Path, command: str) -> str:
    if preferred.exists():
        return str(preferred)
    found = shutil.which(command)
    return found or command


def node_command() -> str:
    return command_path(PREFERRED_NODE_EXE, "node")


def npm_command() -> str:
    return command_path(PREFERRED_NPM_CMD, "npm.cmd" if os.name == "nt" else "npm")


def tool_version(command: str, args: list[str] | None = None) -> dict[str, Any]:
    argv = [command, *(args or ["--version"])]
    result = run_capture(argv, timeout=15)
    return {
        "command": command,
        "ok": result.ok,
        "returncode": result.returncode,
        "version": result.stdout.splitlines()[0] if result.stdout else "",
        "stderr": result.stderr[:300],
    }


def free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def fetch_via_proxy(proxy_url: str, url: str, *, timeout: int, max_bytes: int) -> dict[str, Any]:
    started = time.perf_counter()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "codex-network-gateway-lab/1.0"})
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(max_bytes)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            return {
                "ok": True,
                "url": url,
                "status": getattr(response, "status", 0),
                "elapsed_ms": elapsed_ms,
                "bytes": len(body),
                "error": "",
            }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "status": 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "bytes": 0,
            "error": str(exc)[:300],
        }


def localhost_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def ensure_proxy_chain_package(lab_dir: Path) -> dict[str, Any]:
    package_json = lab_dir / "package.json"
    if not package_json.exists():
        package_json.write_text(
            json.dumps(
                {
                    "private": True,
                    "type": "module",
                    "dependencies": {"proxy-chain": "^2.6.0"},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    installed_marker = lab_dir / "node_modules" / "proxy-chain" / "package.json"
    if installed_marker.exists():
        return {"ok": True, "installed": True, "install_ran": False, "stderr": ""}
    result = run_capture([npm_command(), "install", "--no-audit", "--no-fund"], cwd=lab_dir, timeout=120)
    return {
        "ok": result.ok and installed_marker.exists(),
        "installed": installed_marker.exists(),
        "install_ran": True,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-500:],
        "stderr_tail": result.stderr[-500:],
    }


def write_proxy_chain_script(lab_dir: Path) -> Path:
    script = lab_dir / "proxy_chain_smoke.mjs"
    script.write_text(
        """
import { Server } from 'proxy-chain';

const port = Number(process.env.CODEX_LAB_PROXY_PORT || 0);
const upstreamProxyUrl = process.env.CODEX_LAB_UPSTREAM_PROXY || '';
const server = new Server({
  host: '127.0.0.1',
  port,
  verbose: false,
  prepareRequestFunction: () => ({ upstreamProxyUrl }),
});

await server.listen();
console.log(JSON.stringify({
  ok: true,
  schema: 'network_gateway_component_lab.proxy_chain_child.v1',
  port: server.port,
  upstreamProxyUrl,
}));

const shutdown = async () => {
  await server.close(true);
  process.exit(0);
};
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
await new Promise(() => {});
""".lstrip(),
        encoding="utf-8",
    )
    return script


def snapshot() -> dict[str, Any]:
    node = node_command()
    npm = npm_command()
    return {
        "schema": f"{SCHEMA_PREFIX}.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "runtime_dir": str(RUNTIME_DIR),
        "tools": {
            "node": tool_version(node),
            "npm": tool_version(npm),
            "docker": tool_version(shutil.which("docker") or "docker", ["--version"]),
            "gost": tool_version(shutil.which("gost") or "gost"),
            "proxy_chain_cli": tool_version(shutil.which("proxy-chain") or "proxy-chain"),
        },
        "safety": {
            "lab_only": True,
            "writes_global_network_state": False,
            "writes_system_proxy": False,
            "writes_clash_config": False,
        },
    }


def component_plan() -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA_PREFIX}.component_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "adopt_now": [
            {
                "component": "isolated_mihomo",
                "use": "temporary per-node request lease without changing the main Clash node",
                "owner": "_bridge/clash_mihomo_control.py isolated-probe",
            },
            {
                "component": "proxy-chain",
                "use": "localhost HTTP wrapper around a selected upstream proxy for browser/node callers",
                "owner": "_bridge/network_gateway_component_lab.py proxy-chain-smoke",
            },
        ],
        "defer_until_gap": [
            {
                "component": "GOST",
                "use": "protocol forwarding and proxy-chain CLI wrapper when Python/Node wrappers are insufficient",
                "reason": "not installed; no proven gap requiring it yet",
            },
            {
                "component": "easy_proxies",
                "use": "optional proxy-pool backend",
                "reason": "duplicates part of Clash/mihomo and is heavier than current need",
            },
            {
                "component": "Resin",
                "use": "scheduling, sticky lease, circuit breaker, observability design source",
                "reason": "too heavy for first-stage local gateway",
            },
        ],
    }


def proxy_chain_smoke(upstream_proxy: str, test_url: str, timeout: int) -> dict[str, Any]:
    lab_dir = RUNTIME_DIR / "proxy_chain"
    lab_dir.mkdir(parents=True, exist_ok=True)
    install = ensure_proxy_chain_package(lab_dir)
    if not install.get("ok"):
        return {
            "schema": f"{SCHEMA_PREFIX}.proxy_chain_smoke.v1",
            "ok": False,
            "generated_at": now_iso(),
            "stage": "install",
            "install": install,
            "writes_global_network_state": False,
        }
    script = write_proxy_chain_script(lab_dir)
    port = free_local_port()
    env = os.environ.copy()
    env["CODEX_LAB_PROXY_PORT"] = str(port)
    env["CODEX_LAB_UPSTREAM_PROXY"] = upstream_proxy
    proc: subprocess.Popen[str] | None = None
    child_stdout = ""
    child_stderr = ""
    child_ready = False
    startup_error = ""
    try:
        proc = subprocess.Popen(
            [node_command(), str(script)],
            cwd=str(lab_dir),
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=hidden_creationflags(),
        )
        deadline = time.time() + 12
        while time.time() < deadline:
            if proc.poll() is not None:
                child_stdout = (proc.stdout.read() if proc.stdout else "")[:500]
                child_stderr = (proc.stderr.read() if proc.stderr else "")[:500]
                startup_error = child_stderr or child_stdout or "proxy-chain child exited early"
                break
            if localhost_port_open(port):
                child_ready = True
                break
            time.sleep(0.1)
        if not child_ready and not startup_error:
            startup_error = "proxy-chain child did not become ready"
        check = fetch_via_proxy(f"http://127.0.0.1:{port}", test_url, timeout=timeout, max_bytes=4096) if child_ready else {}
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    return {
        "schema": f"{SCHEMA_PREFIX}.proxy_chain_smoke.v1",
        "ok": bool(child_ready) and bool(check.get("ok")) and not startup_error,
        "generated_at": now_iso(),
        "upstream_proxy": upstream_proxy,
        "local_proxy": f"http://127.0.0.1:{port}" if child_ready else "",
        "test_url": test_url,
        "install": install,
        "child_ready": child_ready,
        "child_stdout": child_stdout,
        "child_stderr": child_stderr,
        "startup_error": startup_error,
        "check": check,
        "cleanup": "child process terminated",
        "writes_global_network_state": False,
        "writes_system_proxy": False,
        "writes_clash_config": False,
    }


def gost_plan() -> dict[str, Any]:
    release_url = "https://github.com/go-gost/gost/releases/latest"
    return {
        "schema": f"{SCHEMA_PREFIX}.gost_plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "installed": bool(shutil.which("gost")),
        "recommended_install": {
            "scope": "local_lab_tool",
            "directory": str(RUNTIME_DIR / "tools" / "gost"),
            "source": release_url,
            "verify": "download release checksum before first use",
            "production_use": "defer until a protocol wrapper gap is proven",
        },
        "why": [
            "GOST can expose localhost-only forwarding endpoints",
            "It can bridge HTTP/SOCKS and proxy chains without changing system proxy",
            "It is unnecessary if isolated mihomo plus proxy-chain covers current callers",
        ],
    }


def validate() -> dict[str, Any]:
    snap = snapshot()
    node_ok = bool(snap["tools"]["node"]["ok"])
    npm_ok = bool(snap["tools"]["npm"]["ok"])
    return {
        "schema": f"{SCHEMA_PREFIX}.validate.v1",
        "ok": node_ok and npm_ok,
        "generated_at": now_iso(),
        "node_ok": node_ok,
        "npm_ok": npm_ok,
        "lab_runtime_dir": str(RUNTIME_DIR),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex network gateway component lab")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("component-plan")
    sub.add_parser("gost-plan")
    smoke = sub.add_parser("proxy-chain-smoke")
    smoke.add_argument("--upstream-proxy", default=DEFAULT_UPSTREAM_PROXY)
    smoke.add_argument("--test-url", default=DEFAULT_TEST_URL)
    smoke.add_argument("--timeout", type=int, default=20)
    sub.add_parser("validate")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.cmd == "snapshot":
            emit(snapshot())
        elif args.cmd == "component-plan":
            emit(component_plan())
        elif args.cmd == "gost-plan":
            emit(gost_plan())
        elif args.cmd == "proxy-chain-smoke":
            emit(proxy_chain_smoke(args.upstream_proxy, args.test_url, args.timeout))
        elif args.cmd == "validate":
            emit(validate())
        return 0
    except Exception as exc:
        emit({"schema": f"{SCHEMA_PREFIX}.error.v1", "ok": False, "reason": str(exc)[:500]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
