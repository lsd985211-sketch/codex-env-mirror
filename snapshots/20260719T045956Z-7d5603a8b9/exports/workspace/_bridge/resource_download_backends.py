#!/usr/bin/env python3
"""Process-backed download helpers for resource materialization.

Ownership: provide bounded optional download backends such as curl for large or
resumable URL materialization.
Non-goals: choosing resource policy, bypassing network gateway routes, changing
system proxy settings, installing aria2/curl, or running a persistent daemon.
State behavior: writes only caller-selected temporary/download files.
Caller context: `resource_fetcher.py` uses this module after policy and network
route selection have already happened.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BackendAvailability:
    curl_path: str = ""
    aria2c_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "resource_download_backends.availability.v1",
            **asdict(self),
            "curl_available": bool(self.curl_path),
            "aria2c_available": bool(self.aria2c_path),
            "aria2_policy": "usable_when_selected_by_resource_strategy_or_explicit_backend",
        }


@dataclass(frozen=True)
class BackendDownloadResult:
    ok: bool
    backend: str
    path: str = ""
    bytes_read: int = 0
    elapsed_seconds: float = 0.0
    resumed: bool = False
    resume_requested: bool = False
    command: list[str] = field(default_factory=list)
    returncode: int = 0
    error_class: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "resource_download_backends.result.v1",
            **asdict(self),
        }


def availability() -> BackendAvailability:
    return BackendAvailability(curl_path=shutil.which("curl") or "", aria2c_path=shutil.which("aria2c") or "")


def hidden_creationflags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_curl_failure(returncode: int, stderr: str) -> str:
    text = str(stderr or "").lower()
    if returncode == 22 or "404" in text or "403" in text or "http" in text and "error" in text:
        return "http_status"
    if returncode in {6, 7} or "could not resolve" in text or "failed to connect" in text:
        return "network_error"
    if returncode in {18, 33} or "range" in text or "resume" in text:
        return "resume_not_supported"
    if returncode == 28 or "timed out" in text or "timeout" in text:
        return "timeout"
    if returncode == 23 or "write" in text:
        return "filesystem_write_failed"
    return "curl_failed"


def curl_command(
    *,
    curl_path: str,
    url: str,
    output_path: Path,
    timeout_seconds: int,
    proxy_url: str = "",
    resume: bool = False,
) -> list[str]:
    command = [
        curl_path,
        "--location",
        "--fail",
        "--show-error",
        "--silent",
        "--connect-timeout",
        str(max(1, min(int(timeout_seconds or 30), 30))),
        "--max-time",
        str(max(1, int(timeout_seconds or 30))),
        "--output",
        str(output_path),
    ]
    if resume:
        command.extend(["--continue-at", "-"])
    if proxy_url:
        command.extend(["--proxy", proxy_url])
    else:
        command.extend(["--noproxy", "*"])
    command.append(url)
    return command


def aria2_command(
    *,
    aria2c_path: str,
    url: str,
    output_path: Path,
    timeout_seconds: int,
    proxy_url: str = "",
    resume: bool = True,
) -> list[str]:
    command = [
        aria2c_path,
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--continue=true" if resume else "--continue=false",
        "--max-connection-per-server=4",
        "--split=4",
        "--min-split-size=1M",
        "--connect-timeout",
        str(max(1, min(int(timeout_seconds or 30), 30))),
        "--timeout",
        str(max(1, int(timeout_seconds or 30))),
        "--max-tries=1",
        "--dir",
        str(output_path.parent),
        "--out",
        output_path.name,
    ]
    if proxy_url:
        command.extend(["--all-proxy", proxy_url])
    else:
        command.extend(["--no-proxy", "*"])
    command.append(url)
    return command


def run_curl_download(
    *,
    url: str,
    partial_path: Path,
    timeout_seconds: int,
    proxy_url: str = "",
    resume: bool = True,
) -> BackendDownloadResult:
    curl_path = availability().curl_path
    if not curl_path:
        return BackendDownloadResult(ok=False, backend="curl", error_class="backend_unavailable", error="curl executable not found")
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    resume_requested = bool(resume and partial_path.exists() and partial_path.stat().st_size > 0)
    command = curl_command(
        curl_path=curl_path,
        url=url,
        output_path=partial_path,
        timeout_seconds=timeout_seconds,
        proxy_url=proxy_url,
        resume=resume_requested,
    )
    started = time.monotonic()
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1, int(timeout_seconds or 30)) + 5,
        creationflags=hidden_creationflags(),
    )
    elapsed = time.monotonic() - started
    if proc.returncode != 0 and resume_requested and classify_curl_failure(proc.returncode, proc.stderr) == "resume_not_supported":
        try:
            partial_path.unlink(missing_ok=True)
        except OSError:
            pass
        return run_curl_download(
            url=url,
            partial_path=partial_path,
            timeout_seconds=timeout_seconds,
            proxy_url=proxy_url,
            resume=False,
        )
    if proc.returncode != 0:
        return BackendDownloadResult(
            ok=False,
            backend="curl",
            elapsed_seconds=elapsed,
            resume_requested=resume_requested,
            command=command,
            returncode=proc.returncode,
            error_class=classify_curl_failure(proc.returncode, proc.stderr),
            error=(proc.stderr or proc.stdout or "curl failed")[-1000:],
            metadata={"stdout_tail": (proc.stdout or "")[-500:], "stderr_tail": (proc.stderr or "")[-1000:]},
        )
    size = partial_path.stat().st_size if partial_path.exists() else 0
    return BackendDownloadResult(
        ok=True,
        backend="curl",
        path=str(partial_path),
        bytes_read=size,
        elapsed_seconds=elapsed,
        resumed=resume_requested,
        resume_requested=resume_requested,
        command=command,
        returncode=proc.returncode,
        metadata={"sha256": sha256_file(partial_path) if partial_path.exists() else ""},
    )


def classify_aria2_failure(returncode: int, stderr: str) -> str:
    text = str(stderr or "").lower()
    if "status=404" in text or "status=403" in text or "http response header was bad" in text:
        return "http_status"
    if "could not resolve" in text or "connection refused" in text or "network problem" in text:
        return "network_error"
    if "timeout" in text:
        return "timeout"
    if "not enough disk" in text or "permission denied" in text or "could not create file" in text:
        return "filesystem_write_failed"
    if returncode == 3:
        return "resource_not_found"
    return "aria2_failed"


def run_aria2_download(
    *,
    url: str,
    partial_path: Path,
    timeout_seconds: int,
    proxy_url: str = "",
    resume: bool = True,
) -> BackendDownloadResult:
    aria2c_path = availability().aria2c_path
    if not aria2c_path:
        return BackendDownloadResult(ok=False, backend="aria2", error_class="backend_unavailable", error="aria2c executable not found")
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    resume_requested = bool(resume and partial_path.exists() and partial_path.stat().st_size > 0)
    command = aria2_command(
        aria2c_path=aria2c_path,
        url=url,
        output_path=partial_path,
        timeout_seconds=timeout_seconds,
        proxy_url=proxy_url,
        resume=resume,
    )
    started = time.monotonic()
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(1, int(timeout_seconds or 30)) + 10,
        creationflags=hidden_creationflags(),
    )
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        return BackendDownloadResult(
            ok=False,
            backend="aria2",
            elapsed_seconds=elapsed,
            resume_requested=resume_requested,
            command=command,
            returncode=proc.returncode,
            error_class=classify_aria2_failure(proc.returncode, proc.stderr or proc.stdout),
            error=(proc.stderr or proc.stdout or "aria2 failed")[-1000:],
            metadata={"stdout_tail": (proc.stdout or "")[-1000:], "stderr_tail": (proc.stderr or "")[-1000:]},
        )
    size = partial_path.stat().st_size if partial_path.exists() else 0
    return BackendDownloadResult(
        ok=True,
        backend="aria2",
        path=str(partial_path),
        bytes_read=size,
        elapsed_seconds=elapsed,
        resumed=resume_requested,
        resume_requested=resume_requested,
        command=command,
        returncode=proc.returncode,
        metadata={"sha256": sha256_file(partial_path) if partial_path.exists() else ""},
    )


def run_backend_download(
    *,
    backend: str,
    url: str,
    partial_path: Path,
    timeout_seconds: int,
    proxy_url: str = "",
    resume: bool = True,
) -> BackendDownloadResult:
    selected = str(backend or "curl").strip().lower()
    if selected == "aria2":
        return run_aria2_download(
            url=url,
            partial_path=partial_path,
            timeout_seconds=timeout_seconds,
            proxy_url=proxy_url,
            resume=resume,
        )
    return run_curl_download(
        url=url,
        partial_path=partial_path,
        timeout_seconds=timeout_seconds,
        proxy_url=proxy_url,
        resume=resume,
    )


def validate() -> dict[str, Any]:
    available = availability()
    command = curl_command(
        curl_path=available.curl_path or "curl",
        url="https://example.com/file.bin",
        output_path=Path("file.bin.part"),
        timeout_seconds=10,
        proxy_url="",
        resume=True,
    )
    aria2_sample = aria2_command(
        aria2c_path=available.aria2c_path or "aria2c",
        url="https://example.com/file.bin",
        output_path=Path("file.bin.part"),
        timeout_seconds=10,
        proxy_url="",
        resume=True,
    )
    return {
        "schema": "resource_download_backends.validate.v1",
        "ok": "--continue-at" in command and "--noproxy" in command and "--continue=true" in aria2_sample and "--no-proxy" in aria2_sample,
        "availability": available.to_dict(),
        "sample_command_has_resume": "--continue-at" in command,
        "sample_command_no_proxy": "--noproxy" in command,
        "aria2_sample_command_has_resume": "--continue=true" in aria2_sample,
        "aria2_sample_command_no_proxy": "--no-proxy" in aria2_sample,
        "writes_remote_state": False,
        "installs_tools": False,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
