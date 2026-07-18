from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    import msvcrt


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent.parent
OPENCLAW_BASE = PROJECT_ROOT / "_tools" / "openclaw-codex" / "clean-install"
NODE = PROJECT_ROOT / "_tools" / "openclaw-codex" / "node24" / "node-v24.17.0-win-x64" / "node.exe"
OPENCLAW = OPENCLAW_BASE / "openclaw-extract" / "package" / "openclaw.mjs"
STATE_DIR = OPENCLAW_BASE / "state"
HOME_DIR = OPENCLAW_BASE / "home"
LOG_DIR = OPENCLAW_BASE / "logs"
SECRETS_DIR = OPENCLAW_BASE / "secrets"
TOKEN_FILE = SECRETS_DIR / "gateway-token.txt"
LOCK_PATH = ROOT / "runtime" / "openclaw-gateway-hidden-supervisor.lock"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def acquire_lock() -> object | None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+b")
    if os.name == "nt":
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return None
    return handle


def write_life(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"{iso_now()} {message}\n")


def read_or_create_token() -> str:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        existing = TOKEN_FILE.read_text(encoding="utf-8", errors="replace").strip()
        if existing:
            return existing
    raw = secrets.token_bytes(32)
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    TOKEN_FILE.write_text(token, encoding="ascii")
    return token


def port_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Hidden no-console OpenClaw gateway supervisor.")
    parser.add_argument("--port", type=int, default=18789)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--restart-delay-seconds", type=int, default=5)
    args = parser.parse_args()

    lock = acquire_lock()
    if lock is None:
        print(json.dumps({"ok": True, "already_running": True, "supervisor": "openclaw_gateway"}, ensure_ascii=False))
        return 0
    if not NODE.exists():
        raise SystemExit(f"Missing OpenClaw Node runtime: {NODE}")
    if not OPENCLAW.exists():
        raise SystemExit(f"Missing OpenClaw launcher: {OPENCLAW}")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    stdout_log = LOG_DIR / f"openclaw-gateway-loop-{stamp}.stdout.log"
    stderr_log = LOG_DIR / f"openclaw-gateway-loop-{stamp}.stderr.log"
    lifecycle_log = LOG_DIR / f"openclaw-gateway-loop-{stamp}.lifecycle.log"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = dict(os.environ)
    env["OPENCLAW_HOME"] = str(HOME_DIR)
    env["OPENCLAW_STATE_DIR"] = str(STATE_DIR)
    env["OPENCLAW_GATEWAY_TOKEN"] = read_or_create_token()

    write_life(lifecycle_log, f"starting python hidden gateway supervisor port={args.port} restartDelay={args.restart_delay_seconds} node={NODE}")
    write_life(lifecycle_log, f"stdout={stdout_log}")
    write_life(lifecycle_log, f"stderr={stderr_log}")
    write_life(lifecycle_log, f"gateway auth token source={TOKEN_FILE}")

    run = 0
    while True:
        run += 1
        if port_listening(args.host, args.port):
            write_life(lifecycle_log, f"run={run} port={args.port} already listening; rechecking after {args.restart_delay_seconds}s")
            time.sleep(max(1, args.restart_delay_seconds))
            continue

        cmd = [str(NODE), str(OPENCLAW), "gateway", "--port", str(args.port), "--verbose"]
        write_life(lifecycle_log, f"gateway run={run} starting")
        with stdout_log.open("ab") as out, stderr_log.open("ab") as err:
            proc = subprocess.Popen(cmd, cwd=str(OPENCLAW_BASE), stdout=out, stderr=err, creationflags=flags, env=env)
            exit_code = proc.wait()
        write_life(lifecycle_log, f"gateway run={run} exited code={exit_code}; restarting after {args.restart_delay_seconds}s")
        time.sleep(max(1, args.restart_delay_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
