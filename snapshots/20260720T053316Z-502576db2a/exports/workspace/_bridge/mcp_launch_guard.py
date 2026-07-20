#!/usr/bin/env python3
"""Guard Codex stdio MCP launches from startup races.

The guard deliberately does not try to reuse an existing stdio MCP process:
stdio sessions are parent/child pipe contracts. The lock in this file only
serializes the short pre-launch section. It must not be held for the lifetime
of the MCP server, otherwise a later Codex session cannot start its own stdio
server and the tool never reaches the active session.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from shared.process_liveness import process_is_alive as _shared_process_is_alive
except ModuleNotFoundError:
    from _bridge.shared.process_liveness import process_is_alive as _shared_process_is_alive

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "_bridge" / "logs" / "mcp_launch_guard"
LOCK_DIR = ROOT / "_bridge" / "runtime" / "mcp_launch_guard"
LEASE_STALE_SECONDS = 120
LOCK_SCOPE = "prelaunch"
STDIO_EOF_GRACE_SECONDS = 3.0
STDIO_STDOUT_GRACE_SECONDS = 1.0
CHILD_ENV_DEFAULTS = {
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONUNBUFFERED": "1",
}

PROFILE_GROUPS = {
    "cdev": "chrome-devtools",
    "mid": "markitdown-mcp",
    "pw": "playwright",
    "gui": "gui_automation_mcp",
    "skills": "myskills-mcp",
    "cg": "codegraph_mcp",
    "pmb": "local_pmb_proxy",
    "fs": "filesystem_mcp",
    "fs-admin": "filesystem_admin_mcp",
    "slash": "custom_slash_commands_mcp",
    "sqlite-scratch": "sqlite_scratch_mcp",
    "sqlite-bridge-ro": "sqlite_bridge_ro_mcp",
    "msdocs": "microsoftdocs_mcp",
    "oadocs": "openai_docs_mcp",
    "ctx7": "context7_mcp",
    "weixin": "desktop_weixin_mcp",
    "drawio": "next_ai_drawio_mcp",
}

sys.path.insert(0, str(ROOT / "_bridge"))
from codegraph_query_runtime import prelaunch_check as codegraph_runtime_prelaunch_check  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_event(payload: dict[str, Any]) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"mcp-launch-guard-{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception:
        # Never break stdio MCP startup because diagnostic logging failed.
        return


def pid_is_alive(pid: Any) -> bool:
    return _shared_process_is_alive(pid)


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def lock_is_stale(path: Path) -> tuple[bool, str, dict[str, Any]]:
    payload = read_json(path)
    if payload.get("lock_scope") != LOCK_SCOPE:
        return True, "legacy_lifecycle_lock", payload
    pid = payload.get("guard_pid")
    if pid_is_alive(pid):
        return False, "prelaunch_guard_pid_alive", payload
    started = parse_start(payload.get("started_at"))
    if started:
        now = datetime.now(started.tzinfo or timezone.utc)
        age = max(0.0, (now - started).total_seconds())
        if age < LEASE_STALE_SECONDS:
            return False, "recent_lock_without_alive_pid", payload
    return True, "stale_lock", payload


def acquire_profile_lock(profile: str, group: str, command: list[str]) -> tuple[bool, Path, dict[str, Any]]:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCK_DIR / f"{profile}.lock.json"
    payload = {
        "profile": profile,
        "group": group,
        "lock_scope": LOCK_SCOPE,
        "guard_pid": os.getpid(),
        "started_at": utc_now(),
        "command": command,
    }
    for attempt in range(40):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            return True, path, {"ok": True, "attempt": attempt}
        except FileExistsError:
            stale, reason, existing = lock_is_stale(path)
            if stale:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except Exception as exc:
                    return False, path, {"ok": False, "reason": "stale_lock_unlink_failed", "error": str(exc), "existing": existing}
                continue
            time.sleep(0.25)
            continue
        except Exception as exc:
            return False, path, {"ok": False, "reason": "lock_create_failed", "error": str(exc)}
        time.sleep(0.2)
    return False, path, {"ok": False, "reason": "prelaunch_lock_retry_exhausted"}


def release_profile_lock(path: Path) -> None:
    try:
        payload = read_json(path)
        if int(payload.get("guard_pid") or 0) == os.getpid():
            path.unlink(missing_ok=True)
    except Exception:
        return


def parse_start(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.astimezone()
    return parsed


def age_minutes(proc: dict[str, Any], now: datetime) -> float | None:
    started = parse_start(proc.get("start_time"))
    if not started:
        return None
    current = now.astimezone(started.tzinfo) if started.tzinfo else now.replace(tzinfo=None)
    return max(0.0, (current - started).total_seconds() / 60.0)


def prune_before_launch(group: str, min_age_minutes: float, apply: bool) -> dict[str, Any]:
    from resource_process_doctor import PROCESS_PATTERNS, process_snapshot, stop_process_tree

    snapshot = process_snapshot()
    processes = snapshot.get("processes") if isinstance(snapshot.get("processes"), list) else []
    current_pid = os.getpid()
    group_spec = next((item for item in PROCESS_PATTERNS if item.group == group), None)
    if group_spec is None:
        return {"ok": False, "reason": "unknown_group", "group": group}
    if group_spec.protected:
        return {"ok": True, "skipped": True, "reason": "protected_group", "group": group}

    roots = [
        item
        for item in processes
        if item.get("group") == group
        and item.get("instance_root")
        and int(item.get("pid") or 0) != current_pid
    ]
    roots = sorted(roots, key=lambda item: str(item.get("start_time") or ""))
    expected = max(1, int(group_spec.expected_max or 1))
    keep = roots[-expected:] if roots else []
    keep_pids = {int(item.get("pid") or 0) for item in keep}
    candidates = [item for item in roots if int(item.get("pid") or 0) not in keep_pids]

    now = datetime.now(timezone.utc)
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for proc in candidates:
        pid = int(proc.get("pid") or 0)
        proc_age = age_minutes(proc, now)
        if not pid:
            skipped.append({"pid": proc.get("pid"), "reason": "missing_pid"})
            continue
        if proc_age is None:
            skipped.append({"pid": pid, "reason": "start_time_unparseable"})
            continue
        if proc_age < min_age_minutes:
            skipped.append(
                {
                    "pid": pid,
                    "reason": "candidate_too_young",
                    "age_minutes": round(proc_age, 1),
                    "min_age_minutes": min_age_minutes,
                }
            )
            continue
        selected.append(
            {
                "pid": pid,
                "name": proc.get("name"),
                "start_time": proc.get("start_time"),
                "age_minutes": round(proc_age, 1),
            }
        )

    for item in selected:
        results.append({**item, "stop_result": stop_process_tree(item.get("pid"), apply=apply)})

    return {
        "ok": all(bool(item.get("stop_result", {}).get("ok")) for item in results),
        "group": group,
        "apply": bool(apply),
        "min_age_minutes": min_age_minutes,
        "root_count_before_launch": len(roots),
        "expected_max": expected,
        "kept_pids": sorted(keep_pids),
        "selected_count": len(selected),
        "skipped_count": len(skipped),
        "selected": selected,
        "skipped": skipped[:20],
        "results": results,
    }


def is_codegraph_mcp_serve(command: list[str]) -> bool:
    joined = " ".join(str(item) for item in command).lower()
    return ("codegraph" in joined and "serve" in command and "--mcp" in command) or "codegraph_fresh_mcp_server.py" in joined


def codegraph_prelaunch(command: list[str]) -> dict[str, Any]:
    if not is_codegraph_mcp_serve(command):
        return {"ok": True, "skipped": True, "reason": "not_codegraph_command"}
    return codegraph_runtime_prelaunch_check(ROOT)


def profile_prelaunch(profile: str, command: list[str]) -> dict[str, Any]:
    if profile == "cg":
        return codegraph_prelaunch(command)
    return {"ok": True, "skipped": True, "reason": "no_profile_prelaunch"}


def terminate_process_tree(pid: int, *, reason: str) -> dict[str, Any]:
    append_event({"ts": utc_now(), "phase": "terminate_process_tree", "pid": pid, "reason": reason})
    try:
        proc = subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception as exc:
        return {"ok": False, "pid": pid, "reason": reason, "error": str(exc)}
    return {
        "ok": proc.returncode == 0,
        "pid": pid,
        "reason": reason,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip()[-1000:],
        "stderr": (proc.stderr or "").strip()[-1000:],
    }


def build_child_env() -> dict[str, str]:
    """Return a subprocess environment with project-wide stdio encoding defaults."""
    env = os.environ.copy()
    for key, value in CHILD_ENV_DEFAULTS.items():
        env.setdefault(key, value)
    return env


def run_stdio_supervised(profile: str, group: str, command: list[str]) -> int:
    """Proxy stdio to the MCP child and reap it when the client pipe closes.

    Codex talks to stdio MCP servers over the process stdin/stdout streams.
    If Codex closes its side but a wrapper leaves the child alive, later health
    checks can see an apparently live server while the active transport is dead.
    This proxy keeps stdout protocol-clean and turns stdin EOF into child
    stdin close plus bounded process-tree cleanup.
    """

    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            cwd=str(ROOT),
            env=build_child_env(),
            creationflags=creationflags,
        )
    except FileNotFoundError as exc:
        append_event({"ts": utc_now(), "ok": False, "phase": "launch", "profile": profile, "error": str(exc)})
        return 127
    except Exception as exc:
        append_event({"ts": utc_now(), "ok": False, "phase": "launch", "profile": profile, "error": str(exc)})
        return 1

    append_event(
        {
            "ts": utc_now(),
            "phase": "stdio_supervisor_started",
            "profile": profile,
            "group": group,
            "child_pid": child.pid,
            "command": command,
        }
    )

    stdin_eof = threading.Event()
    stdout_done = threading.Event()
    stdout_pipe_closed = threading.Event()
    stdout_source_eof = threading.Event()

    def pump_stdin() -> None:
        try:
            source = sys.stdin.buffer
            target = child.stdin
            if target is None:
                return
            while True:
                line = source.readline()
                if not line:
                    break
                try:
                    target.write(line)
                    target.flush()
                except (BrokenPipeError, OSError):
                    break
        finally:
            stdin_eof.set()
            try:
                if child.stdin:
                    child.stdin.close()
            except Exception:
                pass

    def pump_stdout() -> None:
        try:
            source = child.stdout
            target = sys.stdout.buffer
            if source is None:
                return
            while True:
                line = source.readline()
                if not line:
                    stdout_source_eof.set()
                    break
                try:
                    target.write(line)
                    target.flush()
                except (BrokenPipeError, OSError):
                    stdout_pipe_closed.set()
                    break
        finally:
            stdout_done.set()

    stdin_thread = threading.Thread(target=pump_stdin, name=f"{profile}-stdin-pump", daemon=True)
    stdout_thread = threading.Thread(target=pump_stdout, name=f"{profile}-stdout-pump", daemon=True)
    stdin_thread.start()
    stdout_thread.start()

    while True:
        returncode = child.poll()
        if returncode is not None:
            stdout_thread.join(timeout=2)
            append_event(
                {
                    "ts": utc_now(),
                    "phase": "stdio_supervisor_child_exit",
                    "profile": profile,
                    "group": group,
                    "child_pid": child.pid,
                    "returncode": returncode,
                    "stdin_eof": stdin_eof.is_set(),
                    "stdout_done": stdout_done.is_set(),
                }
            )
            return int(returncode or 0)
        if stdin_eof.is_set():
            try:
                returncode = child.wait(timeout=STDIO_EOF_GRACE_SECONDS)
                stdout_thread.join(timeout=2)
                append_event(
                    {
                        "ts": utc_now(),
                        "phase": "stdio_supervisor_eof_child_exit",
                        "profile": profile,
                        "group": group,
                        "child_pid": child.pid,
                        "returncode": returncode,
                    }
                )
                return int(returncode or 0)
            except subprocess.TimeoutExpired:
                stop = terminate_process_tree(child.pid, reason="stdio_client_eof_child_still_alive")
                append_event(
                    {
                        "ts": utc_now(),
                        "phase": "stdio_supervisor_eof_cleanup",
                        "profile": profile,
                        "group": group,
                        "child_pid": child.pid,
                        "result": stop,
                    }
                )
                return 0 if stop.get("ok") else 1
        if stdout_done.is_set():
            time.sleep(STDIO_STDOUT_GRACE_SECONDS)
            returncode = child.poll()
            if returncode is not None:
                append_event(
                    {
                        "ts": utc_now(),
                        "phase": "stdio_supervisor_stdout_done_child_exit",
                        "profile": profile,
                        "group": group,
                        "child_pid": child.pid,
                        "returncode": returncode,
                        "stdin_eof": stdin_eof.is_set(),
                        "stdout_pipe_closed": stdout_pipe_closed.is_set(),
                        "stdout_source_eof": stdout_source_eof.is_set(),
                    }
                )
                return int(returncode or 0)
            stop = terminate_process_tree(
                child.pid,
                reason="stdio_stdout_done_child_still_alive",
            )
            append_event(
                {
                    "ts": utc_now(),
                    "phase": "stdio_supervisor_stdout_cleanup",
                    "profile": profile,
                    "group": group,
                    "child_pid": child.pid,
                    "stdin_eof": stdin_eof.is_set(),
                    "stdout_pipe_closed": stdout_pipe_closed.is_set(),
                    "stdout_source_eof": stdout_source_eof.is_set(),
                    "result": stop,
                }
            )
            return 0 if stop.get("ok") else 1
        time.sleep(0.1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Codex MCP launch guard")
    parser.add_argument("--profile", required=True, choices=sorted(PROFILE_GROUPS))
    parser.add_argument("--min-age-minutes", type=float, default=15.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--prune-before-launch",
        action="store_true",
        help="Opt-in legacy cleanup. Disabled by default because active stdio MCP sessions cannot be reused by later sessions.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        append_event({"ts": utc_now(), "ok": False, "reason": "missing_command", "profile": args.profile})
        return 64

    group = PROFILE_GROUPS[args.profile]
    lock_ok, lock_path, lock_result = acquire_profile_lock(args.profile, group, command)
    append_event({"ts": utc_now(), "phase": "profile_lock", "profile": args.profile, "group": group, "result": lock_result})
    if not lock_ok:
        return 75
    try:
        if args.prune_before_launch:
            prune = prune_before_launch(group, float(args.min_age_minutes), apply=not bool(args.dry_run))
        else:
            prune = {
                "ok": True,
                "skipped": True,
                "reason": "prelaunch_prune_disabled_for_stdio_safety",
                "group": group,
                "apply": False,
            }
        append_event({"ts": utc_now(), "phase": "prelaunch_prune", "profile": args.profile, "group": group, "result": prune})
        preflight = profile_prelaunch(args.profile, command)
        append_event({"ts": utc_now(), "phase": "profile_prelaunch", "profile": args.profile, "group": group, "result": preflight})
        if not preflight.get("ok"):
            return 75
    except FileNotFoundError as exc:
        append_event({"ts": utc_now(), "ok": False, "phase": "prelaunch_prune", "profile": args.profile, "error": str(exc)})
        return 127
    except Exception as exc:
        append_event({"ts": utc_now(), "ok": False, "phase": "prelaunch_prune", "profile": args.profile, "error": str(exc)})
        return 1
    finally:
        release_profile_lock(lock_path)

    return run_stdio_supervised(args.profile, group, command)


if __name__ == "__main__":
    raise SystemExit(main())
