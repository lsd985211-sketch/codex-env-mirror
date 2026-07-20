"""PMB process lifecycle owner.

Ownership: hidden Windows PMB launch selection and cross-process daemon lock.
Non-goals: PMB health policy, memory migration, or Codex routing.
State behavior: commands are caller-authorized; lock files keep no live state.
Caller context: ``local_pmb_memory.py`` remains the public facade.
"""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def hidden_creation_kwargs() -> dict[str, int]:
    if os.name != "nt":
        return {}
    return {"creationflags": int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))}


def _command_for(pmb_exe: Path, pmb_pythonw: Path, args: list[str]) -> tuple[list[str], str]:
    daemon_action = tuple(str(item).lower() for item in args[:2])
    if os.name == "nt" and daemon_action in {("daemon", "start"), ("daemon", "restart")}:
        if not pmb_pythonw.exists():
            return [], "pmb_pythonw_missing"
        # Upstream uses sys.executable for the persistent child. Starting the
        # CLI under pythonw prevents the venv launcher from creating conhost.
        return [str(pmb_pythonw), "-m", "pmb.cli", *args], "pythonw_module"
    return [str(pmb_exe), *args], "pmb_entrypoint"


def run_pmb_command(
    *, pmb_exe: Path, pmb_pythonw: Path, args: list[str], cwd: Path,
    env: dict[str, str], timeout: int,
) -> dict[str, Any]:
    command, launcher = _command_for(pmb_exe, pmb_pythonw, args)
    if not command:
        return {"ok": False, "reason": launcher, "command": str(pmb_pythonw), "args": args}
    try:
        proc = subprocess.run(
            command, cwd=str(cwd), env=env, text=True, capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            **hidden_creation_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False, "timed_out": True, "launcher": launcher, "args": args,
            "stdout": exc.stdout or "", "stderr": exc.stderr or "",
        }
    except OSError as exc:
        return {
            "ok": False, "reason": f"{type(exc).__name__}: {exc}",
            "launcher": launcher, "args": args,
        }
    return {
        "ok": proc.returncode == 0, "returncode": proc.returncode,
        "launcher": launcher, "args": args,
        "stdout": proc.stdout or "", "stderr": proc.stderr or "",
    }


@contextmanager
def exclusive_process_lock(path: Path, *, timeout_seconds: float = 45.0) -> Iterator[dict[str, Any]]:
    """Acquire a one-byte inter-process lock without deleting its stable file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    started = time.monotonic()
    acquired = False
    try:
        while not acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                if time.monotonic() - started >= timeout_seconds:
                    raise TimeoutError(f"PMB daemon lifecycle lock timed out: {path}")
                time.sleep(0.05)
        yield {"path": str(path), "waited_ms": round((time.monotonic() - started) * 1000.0, 3)}
    finally:
        if acquired:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()
