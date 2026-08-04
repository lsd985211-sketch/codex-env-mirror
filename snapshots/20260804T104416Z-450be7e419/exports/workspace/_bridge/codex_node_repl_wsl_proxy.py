#!/usr/bin/env python3
"""Project WSL sandbox metadata before starting the Windows node_repl server."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping
from urllib.parse import quote, unquote, urlsplit


SANDBOX_META_KEY = "codex/sandbox-state-meta"
DEFAULT_DISTRO = "Codex-Wsl-Lab"
MINIMUM_NODE_VERSION = (22, 22, 0)


def wsl_path(value: str) -> Path:
    text = str(value or "").strip()
    if len(text) >= 3 and text[1:3] in {":\\", ":/"} and text[0].isalpha():
        suffix = text[3:].replace("\\", "/")
        return Path("/mnt") / text[0].lower() / suffix
    return Path(text).expanduser()


def windows_path(value: Path) -> str:
    parts = value.resolve().parts
    if len(parts) >= 4 and parts[:2] == ("/", "mnt") and len(parts[2]) == 1:
        suffix = "\\".join(parts[3:])
        return f"{parts[2].upper()}:\\{suffix}"
    return str(value)


def read_node_version(executable: Path) -> tuple[int, int, int] | None:
    try:
        process = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if process.returncode != 0:
        return None
    value = (process.stdout or process.stderr or "").strip().lstrip("v")
    try:
        major, minor, patch = value.split(".", 2)
        return int(major), int(minor), int(patch.split("-", 1)[0])
    except (TypeError, ValueError):
        return None


def default_windows_home() -> Path:
    explicit = str(os.environ.get("NODE_REPL_WINDOWS_HOME") or "").strip()
    if explicit:
        return wsl_path(explicit)
    user_profile = str(os.environ.get("USERPROFILE") or "").strip()
    if user_profile:
        return wsl_path(user_profile)
    return Path("/mnt/c/Users") / str(os.environ.get("USERNAME") or os.environ.get("USER") or "")


def select_node_runtime(
    *,
    windows_home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    version_reader: Callable[[Path], tuple[int, int, int] | None] = read_node_version,
) -> Path | None:
    env = os.environ if environ is None else environ
    home = default_windows_home() if windows_home is None else windows_home
    candidates: list[Path] = []
    explicit = str(env.get("NODE_REPL_NODE_PATH") or "").strip()
    if explicit:
        candidates.append(wsl_path(explicit))
    managed_root = home / "AppData" / "Local" / "OpenAI" / "Codex" / "runtimes" / "cua_node"
    managed = [path for path in managed_root.glob("*/bin/node.exe") if path.is_file()]
    managed.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    candidates.extend(managed)
    candidates.append(Path("/mnt/c/Program Files/nodejs/node.exe"))
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        version = version_reader(candidate)
        if version is not None and version >= MINIMUM_NODE_VERSION:
            return candidate
    return None


def windows_file_uri(value: str, *, distribution: str) -> str:
    parsed = urlsplit(str(value or ""))
    if parsed.scheme.casefold() != "file" or parsed.netloc:
        return value
    linux_path = unquote(parsed.path)
    if not linux_path.startswith("/"):
        return value
    parts = linux_path.split("/", 4)
    if len(parts) >= 4 and parts[1] == "mnt" and len(parts[2]) == 1 and parts[2].isalpha():
        drive = parts[2].upper()
        suffix = f"/{parts[3]}" + (f"/{parts[4]}" if len(parts) > 4 else "")
        return f"file:///{drive}:{quote(suffix, safe='/')}"
    encoded_path = quote(linux_path, safe="/")
    return f"file://wsl.localhost/{quote(distribution, safe='')}{encoded_path}"


def project_sandbox_metadata(value: Any, *, distribution: str) -> int:
    changed = 0
    if isinstance(value, list):
        for item in value:
            changed += project_sandbox_metadata(item, distribution=distribution)
        return changed
    if not isinstance(value, dict):
        return 0
    sandbox = value.get(SANDBOX_META_KEY)
    if isinstance(sandbox, dict) and isinstance(sandbox.get("sandboxCwd"), str):
        current = sandbox["sandboxCwd"]
        projected = windows_file_uri(current, distribution=distribution)
        if projected != current:
            sandbox["sandboxCwd"] = projected
            changed += 1
    for key, item in value.items():
        if key != SANDBOX_META_KEY:
            changed += project_sandbox_metadata(item, distribution=distribution)
    return changed


def project_json_line(line: bytes, *, distribution: str) -> bytes:
    try:
        payload = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return line
    if not project_sandbox_metadata(payload, distribution=distribution):
        return line
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def copy_stream(source: BinaryIO, destination: BinaryIO) -> None:
    for line in iter(source.readline, b""):
        destination.write(line)
        destination.flush()


def iter_input_lines(source: BinaryIO):
    pending = bytearray()
    descriptor = source.fileno()
    while chunk := os.read(descriptor, 65536):
        pending.extend(chunk)
        while True:
            boundary = pending.find(b"\n")
            if boundary < 0:
                break
            yield bytes(pending[: boundary + 1])
            del pending[: boundary + 1]
    if pending:
        yield bytes(pending)


def run_proxy(*, node_repl: Path, distribution: str) -> int:
    input_lines = iter_input_lines(sys.stdin.buffer)
    try:
        first_line = next(input_lines)
    except StopIteration:
        return 0
    windows_home = node_repl.parents[2] if len(node_repl.parents) > 2 else None
    node_runtime = select_node_runtime(windows_home=windows_home)
    if node_runtime is None:
        minimum = ".".join(str(item) for item in MINIMUM_NODE_VERSION)
        print(f"node_repl WSL bridge: no trusted Node runtime >= {minimum}", file=sys.stderr)
        return 127
    child_env = os.environ.copy()
    child_env["NODE_REPL_NODE_PATH"] = windows_path(node_runtime)
    child = subprocess.Popen(
        [str(node_repl)],
        env=child_env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert child.stdin is not None and child.stdout is not None and child.stderr is not None
    stdout_thread = threading.Thread(target=copy_stream, args=(child.stdout, sys.stdout.buffer), daemon=True)
    stderr_thread = threading.Thread(target=copy_stream, args=(child.stderr, sys.stderr.buffer), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        child.stdin.write(project_json_line(first_line, distribution=distribution))
        child.stdin.flush()
        for line in input_lines:
            child.stdin.write(project_json_line(line, distribution=distribution))
            child.stdin.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            child.stdin.close()
        except OSError:
            pass
    return child.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-repl", type=Path, required=True)
    parser.add_argument("--distribution", default=os.environ.get("WSL_DISTRO_NAME") or DEFAULT_DISTRO)
    args = parser.parse_args(argv)
    if not args.node_repl.is_file():
        return 127
    return run_proxy(node_repl=args.node_repl, distribution=args.distribution)


if __name__ == "__main__":
    raise SystemExit(main())
