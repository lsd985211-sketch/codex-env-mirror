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
from typing import Any, BinaryIO
from urllib.parse import quote, unquote, urlsplit


SANDBOX_META_KEY = "codex/sandbox-state-meta"
DEFAULT_DISTRO = "Codex-Wsl-Lab"


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
    read = getattr(source, "read1", source.read)
    for chunk in iter(lambda: read(65536), b""):
        destination.write(chunk)
        destination.flush()


def run_proxy(*, node_repl: Path, distribution: str) -> int:
    child = subprocess.Popen(
        [str(node_repl)],
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
        for line in sys.stdin.buffer:
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
