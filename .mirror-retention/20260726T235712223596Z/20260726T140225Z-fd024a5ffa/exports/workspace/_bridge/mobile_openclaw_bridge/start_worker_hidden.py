from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if os.name == "nt":
    import msvcrt


ROOT = Path(__file__).resolve().parent
CLI = ROOT / "mobile_openclaw_cli.py"
LOG_DIR = ROOT / "logs"
LOCK_PATH = ROOT / "runtime" / "worker-hidden-supervisor.lock"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def resolve_python_console() -> str:
    env_python = os.environ.get("MOBILE_OPENCLAW_PYTHON")
    if env_python and Path(env_python).exists():
        return env_python
    bundled = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "python" / "python.exe"
    if bundled.exists():
        return str(bundled)
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        sibling = executable.with_name("python.exe")
        if sibling.exists():
            return str(sibling)
    return str(executable)


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


def rotate_logs(keep_recent: int, max_stdout_bytes: int, max_archive_bytes: int) -> None:
    archive = LOG_DIR / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    logs = sorted(LOG_DIR.glob("worker-loop-*.*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for index, log in enumerate(logs, start=1):
        if index <= keep_recent and log.stat().st_size <= max_stdout_bytes:
            continue
        target = archive / log.name
        try:
            log.replace(target)
        except OSError:
            pass
    archived = sorted(archive.glob("worker-loop-*.*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    total = 0
    for index, log in enumerate(archived, start=1):
        size = log.stat().st_size
        total += size
        oversized = log.name.endswith(".stdout.log") and size > max_stdout_bytes
        too_many = index > max(keep_recent * 4, 24)
        too_large = total > max_archive_bytes
        if oversized or too_many or too_large:
            try:
                log.unlink()
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Hidden no-console mobile OpenClaw worker supervisor.")
    parser.add_argument("--interval-seconds", type=int, default=1)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--restart-delay-seconds", type=int, default=5)
    parser.add_argument("--log-mode", choices=["summary", "full", "quiet"], default="summary")
    parser.add_argument("--keep-recent-worker-logs", type=int, default=12)
    parser.add_argument("--max-worker-stdout-bytes", type=int, default=20 * 1024 * 1024)
    parser.add_argument("--max-archive-bytes", type=int, default=64 * 1024 * 1024)
    args = parser.parse_args()

    lock = acquire_lock()
    if lock is None:
        print(json.dumps({"ok": True, "already_running": True, "supervisor": "worker"}, ensure_ascii=False))
        return 0

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = timestamp()
    stdout_log = LOG_DIR / f"worker-loop-{stamp}.stdout.log"
    stderr_log = LOG_DIR / f"worker-loop-{stamp}.stderr.log"
    lifecycle_log = LOG_DIR / f"worker-loop-{stamp}.lifecycle.log"
    python = resolve_python_console()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    rotate_logs(args.keep_recent_worker_logs, args.max_worker_stdout_bytes, args.max_archive_bytes)
    write_life(lifecycle_log, f"starting python hidden worker supervisor interval={args.interval_seconds} limit={args.limit} restartDelay={args.restart_delay_seconds} logMode={args.log_mode} python={python}")
    write_life(lifecycle_log, f"stdout={stdout_log}")
    write_life(lifecycle_log, f"stderr={stderr_log}")

    run = 0
    while True:
        run += 1
        cmd = [
            python,
            str(CLI),
            "worker-loop",
            "--interval",
            str(args.interval_seconds),
            "--limit",
            str(args.limit),
            "--log-mode",
            args.log_mode,
        ]
        write_life(lifecycle_log, f"worker-loop run={run} starting")
        with stdout_log.open("ab") as out, stderr_log.open("ab") as err:
            proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=out, stderr=err, creationflags=flags, env=env)
            exit_code = proc.wait()
        write_life(lifecycle_log, f"worker-loop run={run} exited code={exit_code}; restarting after {args.restart_delay_seconds}s")
        time.sleep(max(1, args.restart_delay_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
