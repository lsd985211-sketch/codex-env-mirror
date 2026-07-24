#!/usr/bin/env python3
"""Standard-library mirror, validation, and isolated restore staging CLI."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
_configured_git = os.environ.get("CODEX_MIRROR_GIT_EXE", "").strip()
_windows_git = Path(r"C:\Program Files\Git\cmd\git.exe")
GIT_EXE = _configured_git or (str(_windows_git) if _windows_git.is_file() else "git")
MANIFEST_ROOT = ROOT / "manifests"
SOURCE_MANIFEST = MANIFEST_ROOT / "source-authorities.json"
EXTERNAL_ARCHIVES = MANIFEST_ROOT / "external-archives.json"
ASSET_DISPOSITIONS = MANIFEST_ROOT / "asset-dispositions.json"
CONTROL_PLANE_CONTRACT = MANIFEST_ROOT / "control-plane-contract.json"
CONTROL_PLANE_STATE = MANIFEST_ROOT / "control-plane-state.json"
CONTRACT_REVIEW_STATE = MANIFEST_ROOT / "contract-review-state.json"
RESTORE_ORDER = MANIFEST_ROOT / "restore-order.json"
SNAPSHOT_ROOT = ROOT / "snapshots"
RUNTIME_ROOT = ROOT / "runtime"
LATEST_PATH = SNAPSHOT_ROOT / "latest.json"
SNAPSHOT_TRANSACTION_NAME = "snapshot.json"
GOVERNANCE_HASH_MODE = "canonical_utf8_lf_v1"
CURRENT_STATE_PATH = ROOT / "CURRENT.md"

HIGH_CONFIDENCE_SECRET_PATTERNS = (
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
)
SENSITIVE_KEY = re.compile(
    r"(?i)(api[_-]?key|token|password|passwd|secret|authorization|sendkey|client[_-]?secret|private[_-]?key|cookie)"
)
ASSIGNMENT_PATTERN = re.compile(
    r"(?im)^\s*[A-Za-z0-9_.-]*(?:api[_-]?key|token|password|passwd|secret|authorization|sendkey|client[_-]?secret|private[_-]?key|cookie)[A-Za-z0-9_.-]*\s*=\s*[^\r\n]+$"
)
INLINE_SENSITIVE_PATTERN = re.compile(
    r"(?i)(\b[A-Za-z0-9_.-]*(?:api[_-]?key|token|password|passwd|secret|authorization|sendkey|client[_-]?secret|private[_-]?key|cookie)[A-Za-z0-9_.-]*\s*=\s*)(\"[^\"]*\"|'[^']*'|[^,}\r\n]+)"
)
CONFIG_EXTENSIONS = {".cfg", ".ini", ".json", ".toml", ".yaml", ".yml"}
MEMBERSHIP_ASSET_ID = "system-membership-snapshot"
WORK_GIT_RELEASE_SOURCE_ID = "wsl-work-git-release-receipt"
WORK_GIT_SOURCE_ID = "workspace-bridge-source"
MIRROR_SOURCE_READ_ONLY_ENV = {
    "CODEX_MIRROR_SOURCE_READ_ONLY": "1",
    "CODEX_MIRROR_REVERSE_OVERWRITE_BLOCKED": "1",
}


class MirrorOperationBusy(RuntimeError):
    def __init__(self, lock_path: Path, owner: dict[str, Any]) -> None:
        super().__init__("mirror_operation_busy")
        self.lock_path = lock_path
        self.owner = owner


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def read_lock_owner(path: Path) -> dict[str, Any]:
    try:
        payload = load_json(path)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


@contextmanager
def exclusive_operation_lock(path: Path, operation: str) -> Iterable[dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    token = f"{os.getpid()}-{time.time_ns()}"
    owner = {"pid": os.getpid(), "operation": operation, "started_at": now_iso(), "token": token}
    for _ in range(2):
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            existing = read_lock_owner(path)
            if not process_is_alive(int(existing.get("pid") or 0)):
                path.unlink(missing_ok=True)
                continue
            raise MirrorOperationBusy(path, existing)
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(owner, handle, ensure_ascii=False)
                handle.write("\n")
            break
    else:
        raise MirrorOperationBusy(path, read_lock_owner(path))
    try:
        yield owner
    finally:
        current = read_lock_owner(path)
        if current.get("token") == token:
            path.unlink(missing_ok=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False, dir=path.parent) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def snapshot_transaction_path() -> Path:
    return RUNTIME_ROOT / "transactions" / SNAPSHOT_TRANSACTION_NAME


def snapshot_candidate_path(snapshot_id: str) -> Path | None:
    candidate_id = str(snapshot_id or "")
    if not candidate_id or Path(candidate_id).name != candidate_id:
        return None
    root = SNAPSHOT_ROOT.resolve()
    candidate = (root / candidate_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def latest_snapshot_payload() -> dict[str, Any] | None:
    try:
        payload = load_json(LATEST_PATH)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def restore_latest_snapshot_payload(payload: dict[str, Any] | None) -> None:
    if payload is None:
        LATEST_PATH.unlink(missing_ok=True)
        return
    write_json_atomic(LATEST_PATH, payload)


def write_snapshot_transaction(payload: dict[str, Any]) -> None:
    write_json_atomic(snapshot_transaction_path(), payload)


def clear_snapshot_transaction(token: str) -> None:
    path = snapshot_transaction_path()
    current = read_lock_owner(path)
    if current.get("token") == token:
        path.unlink(missing_ok=True)


def _remove_stale_staging_directories() -> list[str]:
    staging_root = RUNTIME_ROOT / "staging"
    if not staging_root.is_dir():
        return []
    removed: list[str] = []
    for candidate in staging_root.iterdir():
        if candidate.is_dir() and not candidate.is_symlink():
            shutil.rmtree(candidate, ignore_errors=True)
            removed.append(candidate.name)
    return sorted(removed)


def recover_interrupted_snapshot_state() -> dict[str, Any]:
    """Recover stale snapshot scratch state after a terminated writer.

    The membership guard constrains what a completed snapshot may contain. This
    journal instead owns the capture transaction: it never removes a valid
    latest snapshot and only deletes a candidate when its journal proves that
    it was not published through the latest pointer.
    """
    path = snapshot_transaction_path()
    if not path.exists():
        return {
            "schema": "codex_mirror.snapshot_recovery.v1",
            "ok": True,
            "recovered": False,
            "removed_staging": _remove_stale_staging_directories(),
            "actions": [],
        }
    transaction = read_lock_owner(path)
    if not transaction:
        return {
            "schema": "codex_mirror.snapshot_recovery.v1",
            "ok": False,
            "recovered": False,
            "reason": "snapshot_transaction_unreadable",
            "journal": str(path),
        }
    if process_is_alive(int(transaction.get("pid") or 0)):
        return {
            "schema": "codex_mirror.snapshot_recovery.v1",
            "ok": True,
            "recovered": False,
            "reason": "snapshot_transaction_active",
            "journal": str(path),
        }

    snapshot_id = str(transaction.get("snapshot_id") or "")
    candidate = snapshot_candidate_path(snapshot_id)
    previous_latest = transaction.get("previous_latest")
    previous_latest = previous_latest if isinstance(previous_latest, dict) else None
    latest = latest_snapshot_payload()
    latest_id = str((latest or {}).get("snapshot_id") or "")
    phase = str(transaction.get("phase") or "")
    actions: list[dict[str, Any]] = []
    removed_staging = _remove_stale_staging_directories()

    manifest_valid = False
    if candidate and candidate.is_dir():
        try:
            manifest_valid = str(load_json(candidate / "snapshot-manifest.json").get("snapshot_id") or "") == snapshot_id
        except (OSError, ValueError, json.JSONDecodeError):
            manifest_valid = False

    if candidate and candidate.is_dir() and latest_id != snapshot_id:
        if candidate.name != str((previous_latest or {}).get("snapshot_id") or ""):
            shutil.rmtree(candidate, ignore_errors=True)
            actions.append({"code": "orphan_snapshot_candidate_removed", "snapshot_id": snapshot_id, "phase": phase})
    elif latest_id == snapshot_id and not manifest_valid:
        restore_latest_snapshot_payload(previous_latest)
        if candidate and candidate.is_dir():
            shutil.rmtree(candidate, ignore_errors=True)
        actions.append({"code": "invalid_latest_candidate_reverted", "snapshot_id": snapshot_id, "phase": phase})
    elif latest_id == snapshot_id and manifest_valid:
        actions.append({"code": "valid_latest_candidate_preserved", "snapshot_id": snapshot_id, "phase": phase})

    token = str(transaction.get("token") or "")
    if token:
        clear_snapshot_transaction(token)
    return {
        "schema": "codex_mirror.snapshot_recovery.v1",
        "ok": True,
        "recovered": bool(actions or removed_staging),
        "removed_staging": removed_staging,
        "actions": actions,
    }


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text_file(path: Path) -> str:
    normalized = read_text(path).replace("\r\n", "\n").replace("\r", "\n")
    return sha256_bytes(normalized.encode("utf-8"))


def expand_tokens(value: str, variables: dict[str, str]) -> str:
    result = str(value)
    for _ in range(8):
        previous = result
        for key, item in variables.items():
            result = result.replace("${" + key + "}", str(item))
        result = os.path.expandvars(result)
        if result == previous:
            break
    return result


def expanded_variables(config: dict[str, Any]) -> dict[str, str]:
    variables = {str(key): str(value) for key, value in config.get("variables", {}).items()}
    for _ in range(8):
        variables = {key: expand_tokens(value, variables) for key, value in variables.items()}
    return variables


def relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"text_decode_failed:{path}")


def scrub_known_tokens(text: str) -> str:
    output = text
    replacements = {
        "openai_key": "<SECRET:OPENAI_API_KEY>",
        "github_token": "<SECRET:GITHUB_AUTH>",
        "google_api_key": "<SECRET:GOOGLE_API_KEY>",
        "bearer_token": "Bearer <SECRET:BEARER_TOKEN>",
        "private_key": "<SECRET:PRIVATE_KEY>",
    }
    for name, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
        output = pattern.sub(replacements[name], output)
    return output


def redact_toml(path: Path) -> bytes:
    text = read_text(path)
    tomllib.loads(text)
    assignment = re.compile(r"^(\s*)([A-Za-z0-9_.-]+)(\s*=\s*)([^\r\n]*)(\r?)$", re.MULTILINE)
    def redact_assignment(match: re.Match[str]) -> str:
        if match and SENSITIVE_KEY.search(match.group(2)):
            secret_id = re.sub(r"[^A-Za-z0-9]+", "_", match.group(2)).upper().strip("_")
            return f'{match.group(1)}{match.group(2)}{match.group(3)}"<SECRET:{secret_id}>"{match.group(5)}'
        return match.group(0)

    # Keep the parser as the syntax guard, but avoid rebuilding every line when
    # the configuration has no redaction candidate.
    needs_redaction = bool(SENSITIVE_KEY.search(text) or any(pattern.search(text) for _, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS))
    if not needs_redaction:
        return text.encode("utf-8")
    redacted = assignment.sub(redact_assignment, text)
    redacted = INLINE_SENSITIVE_PATTERN.sub(
        lambda match: match.group(0) if "<SECRET:" in match.group(2) else match.group(1) + '"<SECRET:REQUIRED>"',
        redacted,
    )
    return scrub_known_tokens(redacted).encode("utf-8")


def redact_url_value(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    port = f":{parsed.port}" if parsed.port else ""
    netloc = hostname + port
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    redacted_query = [
        (key, "<SECRET:" + re.sub(r"[^A-Za-z0-9]+", "_", key).upper().strip("_") + ">")
        if SENSITIVE_KEY.search(key)
        else (key, scrub_known_tokens(item))
        for key, item in query
    ]
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, urllib.parse.urlencode(redacted_query), parsed.fragment))


def redact_string_value(value: str) -> str:
    return redact_url_value(scrub_known_tokens(value))


def redact_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): ("<SECRET:" + re.sub(r"[^A-Za-z0-9]+", "_", str(key)).upper().strip("_") + ">")
            if SENSITIVE_KEY.search(str(key))
            else redact_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_json_value(item) for item in value]
    if isinstance(value, str):
        return redact_string_value(value)
    return value


def redact_json(path: Path) -> bytes:
    payload = json.loads(read_text(path))
    return (json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def secret_findings(text: str, *, path: str, config_file: bool) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for name, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
        if pattern.search(text):
            findings.append({"path": path, "kind": name})
    if config_file:
        for match in ASSIGNMENT_PATTERN.finditer(text):
            if "<SECRET:" not in match.group(0):
                findings.append({"path": path, "kind": "sensitive_assignment"})
                break
    return findings


def iter_source_files(root: Path, spec: dict[str, Any], policy: dict[str, Any]) -> Iterable[Path]:
    excluded_dirs = {str(item).lower() for item in policy.get("global_exclude_dirs", [])}
    excluded_dirs.update(str(item).lower() for item in spec.get("exclude_dirs", []))
    excluded_files = {str(item).lower() for item in policy.get("global_exclude_files", [])}
    excluded_files.update(str(item).lower() for item in spec.get("exclude_files", []))
    allowed = {str(item).lower() for item in policy.get("allowed_extensions", [])}
    allowed.update(str(item).lower() for item in spec.get("extra_allowed_extensions", []))
    binary = {str(item).lower() for item in spec.get("binary_extensions", [])}
    explicitly_allowed = allowed | binary
    prohibited = {str(item).lower() for item in policy.get("prohibited_extensions", [])} - explicitly_allowed
    allowed_filenames = {str(item).lower() for item in spec.get("allowed_filenames", [])}
    allow_extensionless = bool(spec.get("allow_extensionless"))
    max_bytes = int(spec.get("max_file_bytes", policy.get("max_file_bytes", 5 * 1024 * 1024)))
    for current_root, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name.lower() not in excluded_dirs and not (Path(current_root) / name).is_symlink()]
        for name in files:
            path = Path(current_root) / name
            if name.lower() in excluded_files or path.is_symlink():
                continue
            suffix = path.suffix.lower()
            if suffix in prohibited:
                continue
            if name.lower() not in allowed_filenames and suffix not in explicitly_allowed and not (allow_extensionless and not suffix):
                continue
            try:
                if path.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            yield path


def source_content_kind(path: Path, spec: dict[str, Any]) -> str:
    binary = {str(item).lower() for item in spec.get("binary_extensions", [])}
    binary_filenames = {str(item).lower() for item in spec.get("binary_filenames", [])}
    return "binary" if path.suffix.lower() in binary or path.name.lower() in binary_filenames else "text"


def source_payload(path: Path, mode: str, content_kind: str = "text") -> tuple[bytes, str, str]:
    if content_kind == "binary":
        if mode in {"redact_toml", "redact_json"}:
            raise ValueError(f"binary_redaction_mode_invalid:{path}")
        return path.read_bytes(), mode, content_kind
    if mode == "redact_toml":
        return redact_toml(path), mode, content_kind
    if mode == "redact_json":
        return redact_json(path), mode, content_kind
    text = read_text(path)
    scrubbed = scrub_known_tokens(text)
    if scrubbed != text:
        return scrubbed.encode("utf-8"), "copy_with_token_redaction", content_kind
    return path.read_bytes(), mode, content_kind


def add_asset(
    *,
    stage: Path,
    snapshot_path: str,
    data: bytes,
    asset_id: str,
    owner: str,
    classification: str,
    source_path: str = "",
    restore_template: str = "",
    mode: str = "copy",
    content_kind: str = "text",
) -> dict[str, Any]:
    destination = stage / Path(snapshot_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    if content_kind == "text":
        text = read_text(destination)
        findings = secret_findings(text, path=snapshot_path, config_file=destination.suffix.lower() in CONFIG_EXTENSIONS)
        if findings:
            raise ValueError("secret_scan_failed:" + json.dumps(findings, ensure_ascii=False))
    return {
        "asset_id": asset_id,
        "source_path": source_path,
        "snapshot_path": snapshot_path.replace("\\", "/"),
        "restore_template": restore_template,
        "sha256": sha256_bytes(data),
        "bytes": len(data),
        "owner": owner,
        "classification": classification,
        "mode": mode,
        "content_kind": content_kind,
    }


def run_json_command(command: list[str], variables: dict[str, str]) -> bytes:
    expanded = [expand_tokens(item, variables) for item in command]
    environment = os.environ.copy()
    environment.update(MIRROR_SOURCE_READ_ONLY_ENV)
    completed = subprocess.run(
        expanded,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"command_failed:{expanded}:{completed.stderr[-2000:]}")
    payload = json.loads(completed.stdout.lstrip("\ufeff"))
    return (json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def membership_projection_issues(
    config: dict[str, Any],
    variables: dict[str, str] | None = None,
    projection: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    variables = variables or expanded_variables(config)
    if projection is None:
        spec = next((item for item in config.get("generated_sources", []) if item.get("id") == MEMBERSHIP_ASSET_ID), None)
        if not spec:
            return [{"code": "membership_projection_source_missing", "source_id": MEMBERSHIP_ASSET_ID}]
        try:
            projection_payload = json.loads(run_json_command(spec["command"], variables).decode("utf-8"))
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            return [{"code": "membership_projection_unavailable", "detail": f"{type(exc).__name__}:{exc}"}]
        projection = projection_payload.get("mirror_source_projection", {})
    if not isinstance(projection, dict):
        return [{"code": "membership_projection_invalid", "detail": "projection is not an object"}]
    issues = list(projection.get("issues", [])) if isinstance(projection.get("issues"), list) else []
    configured_sources = {str(item.get("id") or "") for item in config.get("sources", []) if str(item.get("id") or "")}
    configured_generated = {str(item.get("id") or "") for item in config.get("generated_sources", []) if str(item.get("id") or "")}
    projected_sources = {str(item) for item in projection.get("source_ids", []) if str(item)}
    projected_generated = {str(item) for item in projection.get("generated_source_ids", []) if str(item)}
    for source_id in sorted(projected_sources - configured_sources):
        issues.append({"code": "membership_projection_source_unknown", "source_id": source_id})
    for source_id in sorted(configured_sources - projected_sources):
        issues.append({"code": "source_missing_membership_owner", "source_id": source_id})
    for source_id in sorted(projected_generated - configured_generated):
        issues.append({"code": "membership_projection_generated_source_unknown", "source_id": source_id})
    for source_id in sorted(configured_generated - projected_generated):
        issues.append({"code": "generated_source_missing_membership_owner", "source_id": source_id})
    return issues


def powershell_json(script: str) -> bytes:
    completed = subprocess.run(
        ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"powershell_export_failed:{completed.stderr[-2000:]}")
    text = completed.stdout.strip().lstrip("\ufeff") or "[]"
    payload = json.loads(text)
    return (json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def export_windows_tasks(patterns: list[str]) -> bytes:
    encoded = ",".join("'" + item.replace("'", "''") + "'" for item in patterns)
    script = rf"""
$patterns=@({encoded})
$items=Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {{
  $name=$_.TaskName; $path=$_.TaskPath
  @($patterns | Where-Object {{ $name -like "*$_*" -or $path -like "*$_*" }}).Count -gt 0
}} | ForEach-Object {{
  [pscustomobject]@{{
    task_path=$_.TaskPath
    task_name=$_.TaskName
    state=$_.State.ToString()
    actions=@($_.Actions | ForEach-Object {{ [pscustomobject]@{{execute=$_.Execute;arguments=$_.Arguments;working_directory=$_.WorkingDirectory}} }})
    triggers=@($_.Triggers | ForEach-Object {{ [pscustomobject]@{{type=$_.CimClass.CimClassName;enabled=$_.Enabled;start_boundary=$_.StartBoundary}} }})
    principal=[pscustomobject]@{{user_id=$_.Principal.UserId;run_level=$_.Principal.RunLevel.ToString();logon_type=$_.Principal.LogonType.ToString()}}
  }}
}}
@($items) | ConvertTo-Json -Depth 8 -Compress
"""
    return powershell_json(script)


def export_windows_shortcuts(patterns: list[str]) -> bytes:
    encoded = ",".join("'" + item.replace("'", "''") + "'" for item in patterns)
    script = rf"""
$patterns=@({encoded})
$shell=New-Object -ComObject WScript.Shell
$roots=@([Environment]::GetFolderPath('DesktopDirectory'),[Environment]::GetFolderPath('CommonDesktopDirectory')) | Select-Object -Unique
$items=foreach($root in $roots){{
  if(-not $root -or -not (Test-Path -LiteralPath $root)){{continue}}
  Get-ChildItem -LiteralPath $root -Filter '*.lnk' -File -ErrorAction SilentlyContinue | Where-Object {{
    $name=$_.BaseName
    @($patterns | Where-Object {{ $name -like "*$_*" }}).Count -gt 0
  }} | ForEach-Object {{
    $shortcut=$shell.CreateShortcut($_.FullName)
    [pscustomobject]@{{name=$_.Name;location=$root;target_path=$shortcut.TargetPath;arguments=$shortcut.Arguments;working_directory=$shortcut.WorkingDirectory;icon_location=$shortcut.IconLocation;window_style=$shortcut.WindowStyle}}
  }}
}}
@($items) | ConvertTo-Json -Depth 6 -Compress
"""
    return powershell_json(script)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def redact_semantic_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"binary_sha256": sha256_bytes(value), "bytes": len(value)}
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped.startswith(("{", "[")):
        try:
            return redact_json_value(json.loads(stripped))
        except json.JSONDecodeError:
            pass
    return redact_string_value(value)


def capture_quiescence_probe(config: dict[str, Any], *, sleep: Any = time.sleep) -> dict[str, Any]:
    """Confirm selected mutable recovery sources are stable before copying a snapshot."""
    policy = config.get("policy") if isinstance(config.get("policy"), dict) else {}
    settings = policy.get("capture_quiescence") if isinstance(policy.get("capture_quiescence"), dict) else {}
    source_ids = [str(item) for item in settings.get("source_ids", []) if str(item)]
    if not source_ids:
        return {"schema": "codex_mirror.capture_quiescence.v1", "ok": True, "enabled": False}
    try:
        sample_count = int(settings.get("sample_count", 2))
        interval_seconds = float(settings.get("interval_seconds", 1.0))
    except (TypeError, ValueError):
        return {"schema": "codex_mirror.capture_quiescence.v1", "ok": False, "reason": "capture_quiescence_policy_invalid"}
    if sample_count < 2 or interval_seconds < 0:
        return {"schema": "codex_mirror.capture_quiescence.v1", "ok": False, "reason": "capture_quiescence_policy_invalid"}
    sources = {str(spec.get("id") or ""): spec for spec in config.get("sources", [])}
    unknown = sorted(set(source_ids) - set(sources))
    if unknown:
        return {
            "schema": "codex_mirror.capture_quiescence.v1", "ok": False,
            "reason": "capture_quiescence_target_unknown", "targets": unknown,
        }
    variables = expanded_variables(config)

    def sample() -> dict[str, str]:
        signatures: dict[str, str] = {}
        for source_id in source_ids:
            spec = sources[source_id]
            source = Path(expand_tokens(str(spec.get("source") or ""), variables))
            if not source.is_file():
                raise FileNotFoundError(f"capture_quiescence_source_not_file:{source_id}")
            content_kind = source_content_kind(source, spec)
            data, _, _ = source_payload(source, str(spec.get("mode") or "copy"), content_kind)
            signatures[source_id] = sha256_bytes(data)
        return signatures

    observed: list[dict[str, str]] = []
    try:
        for index in range(sample_count):
            observed.append(sample())
            if index + 1 < sample_count and interval_seconds:
                sleep(interval_seconds)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "schema": "codex_mirror.capture_quiescence.v1", "ok": False,
            "reason": "capture_quiescence_probe_failed", "detail": f"{type(exc).__name__}:{exc}",
        }
    changed = [
        {"asset_id": asset_id, "before": observed[0][asset_id][:12], "after": observed[-1][asset_id][:12]}
        for asset_id in sorted(observed[0]) if any(item.get(asset_id) != observed[0][asset_id] for item in observed[1:])
    ]
    return {
        "schema": "codex_mirror.capture_quiescence.v1", "ok": not changed, "enabled": True,
        "sample_count": sample_count, "interval_seconds": interval_seconds, "changed": changed,
        "reason": "source_capture_not_quiescent" if changed else "",
    }


def incremental_recapture_ids(config: dict[str, Any]) -> tuple[set[str], set[str]]:
    policy = config.get("policy") if isinstance(config.get("policy"), dict) else {}
    quiescence = policy.get("capture_quiescence") if isinstance(policy.get("capture_quiescence"), dict) else {}
    always_recapture = {
        str(spec.get("id") or "")
        for spec in config.get("generated_sources", [])
        if isinstance(spec, dict) and spec.get("always_recapture") is True and str(spec.get("id") or "")
    }
    return (
        {str(item) for item in quiescence.get("source_ids", []) if str(item)},
        {str(item) for item in quiescence.get("generated_source_ids", []) if str(item)} | always_recapture,
    )


def export_plugin_inventory(config_path: Path, cache_root: Path) -> bytes:
    config = tomllib.loads(read_text(config_path))
    plugins = config.get("plugins", {})
    entries: list[dict[str, Any]] = []
    for identity, settings in sorted(plugins.items() if isinstance(plugins, dict) else []):
        if not isinstance(settings, dict) or settings.get("enabled") is not True:
            continue
        name, separator, marketplace = str(identity).rpartition("@")
        if not separator or not name or not marketplace:
            entries.append({"identity": identity, "enabled": True, "status": "identity_invalid"})
            continue
        plugin_root = cache_root / marketplace / name
        candidates = [
            path
            for path in plugin_root.glob("*/.codex-plugin/plugin.json")
            if path.is_file() and not any(part.startswith("plugin-install-") for part in path.parts)
        ]
        if not candidates:
            entries.append({
                "identity": identity,
                "name": name,
                "marketplace": marketplace,
                "enabled": True,
                "status": "manifest_missing",
                "reacquisition": "install_enabled_plugin_through_codex_plugin_owner",
            })
            continue
        manifest_path = max(candidates, key=lambda path: path.stat().st_mtime_ns)
        manifest = json.loads(read_text(manifest_path))
        entries.append({
            "identity": identity,
            "name": name,
            "marketplace": marketplace,
            "enabled": True,
            "status": "resolved",
            "cache_revision": manifest_path.parents[1].name,
            "manifest_version": manifest.get("version"),
            "manifest_sha256": sha256_file(manifest_path),
            "repository": redact_semantic_value(manifest.get("repository")),
            "license": manifest.get("license"),
            "reacquisition": "install_enabled_plugin_through_codex_plugin_owner_then_verify_manifest_hash_or_version",
        })
    unresolved = [item["identity"] for item in entries if item.get("status") != "resolved"]
    return json_bytes({
        "schema": "codex_mirror.plugin_inventory.v1",
        "generated_at": now_iso(),
        "authority": "derived_from_codex_config_and_plugin_cache",
        "cache_payload_included": False,
        "enabled_count": len(entries),
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "plugins": entries,
    })


def parse_manifest_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections.setdefault(current, [])
        elif current and line.startswith("- "):
            sections[current].append(line[2:].strip())
    return sections


def export_current_checkpoints(manifest_path: Path, shared_root: Path) -> bytes:
    manifest_text = read_text(manifest_path)
    sections = parse_manifest_sections(manifest_text)
    selected: list[dict[str, Any]] = []
    references = sections.get("shared docs", []) + sections.get("recent checkpoints", [])
    for reference in references:
        path = shared_root / Path(reference.replace("/", os.sep))
        if not path.is_file():
            raise FileNotFoundError(f"current_checkpoint_reference_missing:{reference}")
        content = scrub_known_tokens(read_text(path))
        selected.append({
            "path": relative_posix(path, shared_root),
            "sha256": sha256_bytes(content.encode("utf-8")),
            "bytes": len(content.encode("utf-8")),
            "content": content,
        })
    return json_bytes({
        "schema": "codex_mirror.current_checkpoints.v1",
        "generated_at": now_iso(),
        "authority": "derived_from_checkpoint_manifest",
        "selection_rule": "manifest_shared_docs_and_recent_checkpoints_only",
        "full_history_included": False,
        "knowledge_contract_references": sections.get("contracts (knowledge table)", []),
        "manifest_sha256": sha256_bytes(manifest_text.encode("utf-8")),
        "manifest": scrub_known_tokens(manifest_text),
        "selected_count": len(selected),
        "selected": selected,
    })


def export_runtime_versions(variables: dict[str, str]) -> bytes:
    commands = {
        "python": [sys.executable, "--version"],
        "powershell": ["powershell", "-NoLogo", "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
        "git": [GIT_EXE, "--version"],
        "node": ["node", "--version"],
        "npm": ["npm", "--version"],
        "codex": ["codex", "--version"],
    }
    results: dict[str, Any] = {"generated_at": now_iso(), "platform": sys.platform, "commands": {}}
    for name, command in commands.items():
        try:
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
            results["commands"][name] = {
                "ok": completed.returncode == 0,
                "version": (completed.stdout or completed.stderr).strip().splitlines()[:3],
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            results["commands"][name] = {"ok": False, "error": type(exc).__name__}
    desktop_script = r"""
$item=Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction SilentlyContinue | Select-Object -First 1
$result=if($null -eq $item){[pscustomobject]@{ok=$false}}else{[pscustomobject]@{ok=$true;name=$item.Name;version=$item.Version.ToString();publisher_id=$item.PublisherId;architecture=$item.Architecture.ToString()}}
$result | ConvertTo-Json -Compress
"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", desktop_script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        results["codex_desktop"] = json.loads(lines[-1]) if completed.returncode == 0 and lines else {"ok": False}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        results["codex_desktop"] = {"ok": False, "error": type(exc).__name__}
    host_path = Path(variables.get("CODEX_HOME", str(Path.home() / ".codex"))) / "chrome-native-hosts-v2.json"
    compatibility: dict[str, Any] = {"present": host_path.is_file()}
    if host_path.is_file():
        try:
            host = json.loads(read_text(host_path))
            allowed = (
                "schemaVersion",
                "appServerProtocolVersion",
                "appVersion",
                "channel",
                "cliVersion",
                "extensionBuildChannels",
                "nativeHostProtocolVersion",
                "nativeHostVersion",
            )
            profiles = {
                json.dumps({key: entry.get(key) for key in allowed if key in entry}, sort_keys=True)
                for entry in host.get("entries", [])
                if isinstance(entry, dict)
            }
            compatibility = {
                "present": True,
                "schema_version": host.get("schemaVersion"),
                "profiles": [json.loads(item) for item in sorted(profiles)],
                "excluded_runtime_fields": ["entryId", "installId", "paths", "presence", "proxyHost", "proxyPort", "updatedAt"],
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            compatibility = {"present": True, "ok": False, "error": type(exc).__name__}
    results["native_host_compatibility"] = compatibility
    return json_bytes(results)


def governance_hashes() -> dict[str, str]:
    results: dict[str, str] = {}
    for path in sorted(MANIFEST_ROOT.rglob("*.json")):
        if path in {CONTROL_PLANE_STATE, CONTRACT_REVIEW_STATE}:
            continue
        results[relative_posix(path, ROOT)] = sha256_text_file(path)
    for name in ("AGENTS.md", "README.md", "BOOTSTRAP.md", "MIRROR_POLICY.md", "RESTORE.md", "SECURITY.md"):
        path = ROOT / name
        results[name] = sha256_text_file(path)
    for directory in (ROOT / "scripts", ROOT / "tests"):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix.lower() in {".py", ".ps1"}:
                results[relative_posix(path, ROOT)] = sha256_text_file(path)
    return results


def incremental_governance_change_reason(
    previous_manifest: dict[str, Any],
    current_governance_hashes: dict[str, str],
) -> str:
    previous = previous_manifest.get("governance_hashes")
    if not isinstance(previous, dict):
        return "previous_governance_hashes_missing"
    return "" if previous == current_governance_hashes else "governance_contract_changed"


def control_plane_issues(snapshot_id: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    try:
        contract = load_json(CONTROL_PLANE_CONTRACT)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [{"code": "control_plane_contract_unreadable", "detail": str(exc)}]
    if contract.get("schema") != "codex_mirror.control_plane_contract.v1":
        issues.append({"code": "control_plane_contract_schema_invalid", "observed": contract.get("schema")})
    entries = contract.get("files") if isinstance(contract.get("files"), list) else []
    declared = {
        str(item.get("path") or "").replace("\\", "/"): str(item.get("role") or "")
        for item in entries
        if isinstance(item, dict) and item.get("path")
    }
    for required in ("CURRENT.md", "manifests/control-plane-state.json"):
        if declared.get(required) != "generated_current_state":
            issues.append({"code": "control_plane_generated_surface_undeclared", "path": required})
    try:
        state = load_json(CONTROL_PLANE_STATE)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [*issues, {"code": "control_plane_state_unreadable", "detail": str(exc)}]
    if state.get("schema") != "codex_mirror.control_plane_state.v1":
        issues.append({"code": "control_plane_state_schema_invalid", "observed": state.get("schema")})
    if state.get("control_plane_version") != contract.get("control_plane_version"):
        issues.append({
            "code": "control_plane_version_mismatch",
            "state": state.get("control_plane_version"),
            "contract": contract.get("control_plane_version"),
        })
    state_snapshot = state.get("snapshot") if isinstance(state.get("snapshot"), dict) else {}
    if str(state_snapshot.get("snapshot_id") or "") != str(snapshot_id or ""):
        issues.append({
            "code": "control_plane_snapshot_mismatch",
            "state_snapshot_id": state_snapshot.get("snapshot_id"),
            "latest_snapshot_id": snapshot_id,
        })
    state_files = state.get("files") if isinstance(state.get("files"), list) else []
    state_by_path = {
        str(item.get("path") or "").replace("\\", "/"): item
        for item in state_files
        if isinstance(item, dict) and item.get("path")
    }
    for relative, role in declared.items():
        target = ROOT / Path(relative)
        if not target.is_file():
            if role in {"static_contract", "generated_current_state"}:
                issues.append({"code": "control_plane_file_missing", "path": relative, "role": role})
            continue
        if role != "static_contract":
            continue
        row = state_by_path.get(relative)
        if not row:
            issues.append({"code": "control_plane_static_file_untracked", "path": relative})
            continue
        observed = sha256_file(target)
        if row.get("sha256") != observed:
            issues.append({
                "code": "control_plane_static_file_drift",
                "path": relative,
                "expected": row.get("sha256"),
                "observed": observed,
            })
    if CURRENT_STATE_PATH.is_file():
        observed_current = sha256_file(CURRENT_STATE_PATH)
        if state.get("current_md_sha256") != observed_current:
            issues.append({
                "code": "control_plane_current_md_drift",
                "expected": state.get("current_md_sha256"),
                "observed": observed_current,
            })
    return issues


def normalized_path(value: str) -> str:
    return str(value or "").replace("\\", "/").strip("/").lower()


def normalized_member(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def retirement_tombstones(snapshot_root: Path, assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    membership_asset = next((item for item in assets if item.get("asset_id") == MEMBERSHIP_ASSET_ID), None)
    if not membership_asset:
        raise ValueError("system_membership_snapshot_missing")
    payload = load_json(snapshot_root / Path(str(membership_asset["snapshot_path"])))
    rows = payload.get("retirement_tombstones", [])
    if not isinstance(rows, list):
        raise ValueError("retirement_tombstones_invalid")
    return [item for item in rows if isinstance(item, dict) and item.get("lifecycle") == "decommissioned"]


def retired_asset_conflicts(assets: list[dict[str, Any]], tombstones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for asset in assets:
        if asset.get("asset_id") == MEMBERSHIP_ASSET_ID:
            continue
        candidates = [
            str(asset.get("restore_template") or ""),
            str(asset.get("source_path") or ""),
            str(asset.get("snapshot_path") or ""),
        ]
        normalized_candidates = [normalized_path(value) for value in candidates if value]
        basename_tokens = [normalized_member(Path(value.replace("\\", "/")).name) for value in candidates if value]
        for tombstone in tombstones:
            tombstone_id = str(tombstone.get("id") or tombstone.get("member") or "unknown")
            member_token = normalized_member(str(tombstone.get("member") or ""))
            matched_value = ""
            matched_by = ""
            for trace in tombstone.get("active_trace_paths", []):
                trace_path = normalized_path(str(trace.get("path") or "")) if isinstance(trace, dict) else normalized_path(str(trace))
                if trace_path and any(candidate.endswith(trace_path) for candidate in normalized_candidates):
                    matched_value = trace_path
                    matched_by = "active_trace_path"
                    break
            if not matched_by and member_token and any(member_token in token for token in basename_tokens):
                matched_value = member_token
                matched_by = "retired_member_filename"
            if matched_by:
                findings.append({
                    "code": "retired_member_asset",
                    "asset_id": asset.get("asset_id"),
                    "snapshot_path": asset.get("snapshot_path"),
                    "tombstone_id": tombstone_id,
                    "matched_by": matched_by,
                    "matched_value": matched_value,
                })
                break
    return findings


def retired_registration_conflicts(snapshot_root: Path, assets: list[dict[str, Any]], tombstones: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    retired_mcp = {
        str(item.get("member") or "").lower(): str(item.get("id") or "")
        for item in tombstones
        if item.get("system") == "mcp" and item.get("member")
    }
    retired_tasks = {
        str(item.get("member") or "").lower(): str(item.get("id") or "")
        for item in tombstones
        if item.get("system") == "scheduled_task" and item.get("member")
    }
    config_asset = next((item for item in assets if item.get("asset_id") == "codex-config-template"), None)
    if config_asset:
        config_path = snapshot_root / Path(str(config_asset["snapshot_path"]))
        try:
            payload = tomllib.loads(read_text(config_path))
            configured = payload.get("mcp_servers", {})
            if isinstance(configured, dict):
                for name in configured:
                    if str(name).lower() in retired_mcp:
                        findings.append({"code": "retired_mcp_registration", "member": name, "tombstone_id": retired_mcp[str(name).lower()], "asset_id": config_asset["asset_id"]})
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            findings.append({"code": "config_registration_scan_failed", "asset_id": config_asset["asset_id"], "detail": str(exc)})
    task_asset = next((item for item in assets if item.get("asset_id") == "windows-scheduled-tasks"), None)
    if task_asset:
        task_path = snapshot_root / Path(str(task_asset["snapshot_path"]))
        try:
            task_rows = json.loads(read_text(task_path))
            if isinstance(task_rows, list):
                for row in task_rows:
                    name = str(row.get("task_name") or "") if isinstance(row, dict) else ""
                    if name.lower() in retired_tasks:
                        findings.append({"code": "retired_scheduled_task_registration", "member": name, "tombstone_id": retired_tasks[name.lower()], "asset_id": task_asset["asset_id"]})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            findings.append({"code": "scheduled_task_registration_scan_failed", "asset_id": task_asset["asset_id"], "detail": str(exc)})
    return findings


def fingerprint(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def guard_fingerprints(tombstones: list[dict[str, Any]], excluded_assets: list[dict[str, Any]]) -> dict[str, Any]:
    members: dict[str, set[str]] = {}
    paths: set[str] = set()
    for item in tombstones:
        system = str(item.get("system") or "unknown")
        member = normalized_member(str(item.get("member") or ""))
        if member:
            members.setdefault(system, set()).add(fingerprint(member))
        for trace in item.get("active_trace_paths", []):
            trace_path = normalized_path(str(trace.get("path") or "")) if isinstance(trace, dict) else normalized_path(str(trace))
            if trace_path:
                paths.add(fingerprint(trace_path))
    excluded_ids = {str(item.get("asset_id") or "") for item in excluded_assets}
    for asset in excluded_assets:
        for key in ("snapshot_path", "restore_template", "source_path"):
            value = normalized_path(str(asset.get(key) or ""))
            if value:
                paths.add(fingerprint(value))
    return {
        "blocked_member_fingerprints": {key: sorted(values) for key, values in sorted(members.items())},
        "blocked_member_lengths": sorted({len(normalized_member(str(item.get("member") or ""))) for item in tombstones if item.get("member")}),
        "blocked_path_fingerprints": sorted(paths),
        "excluded_asset_fingerprints": sorted(fingerprint(value) for value in excluded_ids if value),
    }


def candidate_path_fingerprints(asset: dict[str, Any]) -> set[str]:
    results: set[str] = set()
    for key in ("snapshot_path", "restore_template", "source_path"):
        value = normalized_path(str(asset.get(key) or ""))
        if not value:
            continue
        parts = value.split("/")
        for index in range(len(parts)):
            suffix = "/".join(parts[index:])
            if suffix:
                results.add(fingerprint(suffix))
    return results


def guarded_asset_conflicts(assets: list[dict[str, Any]], guard: dict[str, Any]) -> list[dict[str, Any]]:
    blocked_paths = {str(item) for item in guard.get("blocked_path_fingerprints", [])}
    blocked_assets = {str(item) for item in guard.get("excluded_asset_fingerprints", [])}
    findings: list[dict[str, Any]] = []
    for asset in assets:
        asset_id_fingerprint = fingerprint(str(asset.get("asset_id") or ""))
        matched = bool(candidate_path_fingerprints(asset) & blocked_paths) or asset_id_fingerprint in blocked_assets
        if matched:
            findings.append({
                "code": "inactive_member_asset_reintroduced",
                "asset_id": asset.get("asset_id"),
                "snapshot_path": asset.get("snapshot_path"),
            })
    return findings


def guarded_registration_conflicts(snapshot_root: Path, assets: list[dict[str, Any]], guard: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    members = guard.get("blocked_member_fingerprints", {})
    blocked_mcp = {str(item) for item in members.get("mcp", [])}
    blocked_tasks = {str(item) for item in members.get("scheduled_task", [])}
    config_asset = next((item for item in assets if item.get("asset_id") == "codex-config-template"), None)
    if config_asset:
        try:
            payload = tomllib.loads(read_text(snapshot_root / Path(str(config_asset["snapshot_path"]))))
            configured = payload.get("mcp_servers", {})
            if isinstance(configured, dict):
                for name in configured:
                    if fingerprint(normalized_member(str(name))) in blocked_mcp:
                        findings.append({"code": "inactive_mcp_registration_reintroduced", "asset_id": config_asset["asset_id"]})
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            findings.append({"code": "config_registration_scan_failed", "asset_id": config_asset["asset_id"], "detail": str(exc)})
    task_asset = next((item for item in assets if item.get("asset_id") == "windows-scheduled-tasks"), None)
    if task_asset:
        try:
            task_rows = json.loads(read_text(snapshot_root / Path(str(task_asset["snapshot_path"]))))
            if isinstance(task_rows, list):
                for row in task_rows:
                    name = str(row.get("task_name") or "") if isinstance(row, dict) else ""
                    if fingerprint(normalized_member(name)) in blocked_tasks:
                        findings.append({"code": "inactive_scheduled_task_reintroduced", "asset_id": task_asset["asset_id"]})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            findings.append({"code": "scheduled_task_registration_scan_failed", "asset_id": task_asset["asset_id"], "detail": str(exc)})
    return findings


def sanitize_membership_export(snapshot_root: Path, assets: list[dict[str, Any]]) -> None:
    membership_asset = next(item for item in assets if item.get("asset_id") == MEMBERSHIP_ASSET_ID)
    membership_path = snapshot_root / Path(str(membership_asset["snapshot_path"]))
    payload = load_json(membership_path)
    payload.pop("retirement_tombstones", None)
    payload.pop("retirement_tombstone_count", None)
    payload["mirror_export_scope"] = "active_members_only"
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    membership_path.write_bytes(data)
    membership_asset["sha256"] = sha256_bytes(data)
    membership_asset["bytes"] = len(data)


def contains_inactive_alias(value: str, aliases: list[str]) -> bool:
    lowered = value.lower()
    return any(alias in lowered for alias in aliases)


def sanitize_json_inactive(value: Any, aliases: list[str]) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if contains_inactive_alias(str(key), aliases):
                continue
            if isinstance(item, (dict, list)):
                cleaned = sanitize_json_inactive(item, aliases)
                result[key] = cleaned
            elif isinstance(item, str) and contains_inactive_alias(item, aliases):
                continue
            else:
                result[key] = item
        return result
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            serialized = json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
            if contains_inactive_alias(serialized, aliases):
                continue
            result.append(sanitize_json_inactive(item, aliases))
        return result
    return value


def sanitize_inactive_references(snapshot_root: Path, assets: list[dict[str, Any]], tombstones: list[dict[str, Any]]) -> int:
    aliases = sorted({
        alias.lower()
        for item in tombstones
        for member in [str(item.get("member") or "")]
        for alias in (member, member.replace("-", "_"), normalized_member(member))
        if alias
    }, key=len, reverse=True)
    changed = 0
    for asset in assets:
        if asset.get("content_kind", "text") != "text":
            continue
        target = snapshot_root / Path(str(asset["snapshot_path"]))
        try:
            text = read_text(target)
        except ValueError:
            continue
        if not contains_inactive_alias(text, aliases):
            continue
        if target.suffix.lower() == ".json":
            payload = json.loads(text)
            cleaned_text = json.dumps(sanitize_json_inactive(payload, aliases), ensure_ascii=False, indent=2) + "\n"
        else:
            lines = text.splitlines()
            cleaned_text = "\n".join(line for line in lines if not contains_inactive_alias(line, aliases)) + "\n"
        data = cleaned_text.encode("utf-8")
        target.write_bytes(data)
        asset["sha256"] = sha256_bytes(data)
        asset["bytes"] = len(data)
        asset["mode"] = str(asset.get("mode") or "copy") + "+inactive_member_sanitized"
        changed += 1
    return changed


def guarded_text_conflicts(text: str, guard: dict[str, Any], path: str) -> list[dict[str, Any]]:
    blocked = {
        str(item)
        for values in guard.get("blocked_member_fingerprints", {}).values()
        for item in values
    }
    lengths = [int(item) for item in guard.get("blocked_member_lengths", []) if int(item) > 0]
    for token in re.findall(r"[A-Za-z0-9_-]+", text):
        normalized = normalized_member(token)
        for length in lengths:
            if len(normalized) < length:
                continue
            for index in range(len(normalized) - length + 1):
                if fingerprint(normalized[index:index + length]) in blocked:
                    return [{"code": "inactive_member_reference_exported", "path": path}]
    return []


def apply_membership_guard(snapshot_root: Path, assets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    tombstones = retirement_tombstones(snapshot_root, assets)
    conflicts = retired_asset_conflicts(assets, tombstones)
    excluded_ids = {str(item["asset_id"]) for item in conflicts}
    kept: list[dict[str, Any]] = []
    for asset in assets:
        if str(asset.get("asset_id")) in excluded_ids:
            target = snapshot_root / Path(str(asset["snapshot_path"]))
            if target.exists():
                target.unlink()
            continue
        kept.append(asset)
    registration_conflicts = retired_registration_conflicts(snapshot_root, kept, tombstones)
    if registration_conflicts:
        raise ValueError("retired_registration_detected:" + json.dumps(registration_conflicts, ensure_ascii=False))
    fingerprints = guard_fingerprints(tombstones, [item for item in assets if str(item.get("asset_id")) in excluded_ids])
    sanitize_membership_export(snapshot_root, kept)
    sanitized_asset_count = sanitize_inactive_references(snapshot_root, kept, tombstones)
    guard = {
        "schema": "codex_mirror.active_membership_guard.v1",
        "membership_asset_id": MEMBERSHIP_ASSET_ID,
        "source_owner_verified": True,
        "membership_export_sanitized": True,
        "excluded_asset_count": len(conflicts),
        "sanitized_asset_count": sanitized_asset_count,
        "registration_conflict_count": 0,
        "activation_policy": "Only active members are recoverable; inactive lifecycle records are consumed during snapshot creation and are not exported.",
        **fingerprints,
    }
    return kept, guard


def path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def collect_asset_dispositions(
    config: dict[str, Any],
    variables: dict[str, str] | None = None,
    *,
    include_assets: bool = False,
) -> dict[str, Any]:
    variables = variables or expanded_variables(config)
    policy = load_json(ASSET_DISPOSITIONS)
    archives = load_json(EXTERNAL_ARCHIVES)
    included_sources = [Path(expand_tokens(str(spec["source"]), variables)).resolve() for spec in config.get("sources", [])]
    external_sources: list[tuple[Path, str, str]] = []
    for item in archives.get("assets", []):
        asset_id = str(item.get("asset_id") or "")
        for value in [item.get("source"), *item.get("related_sources", [])]:
            if value:
                external_sources.append((Path(expand_tokens(str(value), variables)).resolve(), "external_archive", asset_id))
    external_sources.extend(
        (Path(expand_tokens(str(value), variables)).resolve(), disposition, "")
        for field, disposition in (("reacquire_instead_of_archive", "reacquire"), ("regenerate_instead_of_archive", "regenerate"))
        for value in archives.get(field, [])
    )
    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for root_spec in policy.get("roots", []):
        root = Path(expand_tokens(str(root_spec["root"]), variables)).resolve()
        root_rows: list[dict[str, Any]] = []
        if not root.is_dir():
            if root_spec.get("required"):
                issues.append({"code": "inventory_root_missing", "root_id": root_spec["id"], "root": str(root)})
            rows.append({"id": root_spec["id"], "root": str(root), "exists": False, "asset_count": 0, "counts": {}})
            continue
        for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            disposition = ""
            owner = ""
            evidence = ""
            child_resolved = child.resolve()
            source_match = next((path for path in included_sources if child_resolved == path or path_is_within(path, child_resolved)), None)
            if source_match:
                disposition, evidence = "mirrored", str(source_match)
            else:
                external_match = next(
                    ((path, kind, asset_id) for path, kind, asset_id in external_sources if child_resolved == path or path_is_within(path, child_resolved)),
                    None,
                )
                if external_match:
                    path, disposition, owner = external_match
                    evidence = str(path)
                else:
                    for rule in root_spec.get("rules", []):
                        if child.name in rule.get("names", []) or any(fnmatch.fnmatch(child.name, pattern) for pattern in rule.get("patterns", [])):
                            disposition = str(rule["disposition"])
                            owner = str(rule.get("owner") or "")
                            evidence = str(rule.get("evidence") or "explicit_rule")
                            break
            row = {
                "name": child.name,
                "kind": "directory" if child.is_dir() else "file",
                "disposition": disposition or "unclassified",
                "owner": owner,
                "evidence": evidence,
            }
            root_rows.append(row)
            counts[row["disposition"]] = counts.get(row["disposition"], 0) + 1
            if not disposition:
                issues.append({
                    "code": "source_asset_unclassified",
                    "root_id": root_spec["id"],
                    "path": str(child),
                    "name": child.name,
                })
        root_counts: dict[str, int] = {}
        for row in root_rows:
            root_counts[row["disposition"]] = root_counts.get(row["disposition"], 0) + 1
        root_row: dict[str, Any] = {
            "id": root_spec["id"],
            "root": str(root),
            "exists": True,
            "asset_count": len(root_rows),
            "counts": dict(sorted(root_counts.items())),
        }
        if include_assets:
            root_row["assets"] = root_rows
        rows.append(root_row)
    return {
        "schema": "codex_mirror.asset_disposition_inventory.v1",
        "ok": not issues,
        "generated_at": now_iso(),
        "roots": rows,
        "counts": dict(sorted(counts.items())),
        "issues": issues,
    }


def collect_plan(config: dict[str, Any]) -> dict[str, Any]:
    variables = expanded_variables(config)
    policy = config["policy"]
    disposition_inventory = collect_asset_dispositions(config, variables)
    membership_issues = membership_projection_issues(config, variables)
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    total_bytes = 0
    total_files = 0
    for spec in config.get("sources", []):
        source = Path(expand_tokens(spec["source"], variables))
        row = {"id": spec["id"], "kind": spec["kind"], "source": str(source), "required": bool(spec.get("required")), "exists": source.exists(), "files": 0, "bytes": 0}
        if not source.exists():
            if spec.get("required"):
                missing.append(spec["id"])
            rows.append(row)
            continue
        if spec["kind"] == "file":
            row["files"] = 1
            row["bytes"] = source.stat().st_size
        else:
            files = list(iter_source_files(source, spec, policy))
            row["files"] = len(files)
            row["bytes"] = sum(item.stat().st_size for item in files)
        total_files += int(row["files"])
        total_bytes += int(row["bytes"])
        rows.append(row)
    generated_rows: list[dict[str, Any]] = []
    for spec in config.get("generated_sources", []):
        checked: dict[str, bool] = {}
        for field in ("source", "config_source", "cache_root", "manifest_source", "shared_root"):
            if field not in spec:
                continue
            path = Path(expand_tokens(str(spec[field]), variables))
            checked[field] = path.exists()
        exists = all(checked.values()) if checked else True
        if spec.get("required") and not exists:
            missing.append(str(spec["id"]))
        generated_rows.append({
            "id": spec["id"],
            "kind": spec["kind"],
            "required": bool(spec.get("required")),
            "exists": exists,
            "checked_sources": checked,
        })
    missing = sorted(set(missing))
    return {
        "schema": "codex_mirror.plan.v1",
        "ok": not missing and not disposition_inventory["issues"] and not membership_issues and total_bytes <= int(policy["max_snapshot_bytes"]),
        "generated_at": now_iso(),
        "sources": rows,
        "generated_sources": generated_rows,
        "asset_dispositions": disposition_inventory,
        "membership_projection": {
            "ok": not membership_issues,
            "issues": membership_issues,
            "rule": "Every mirror source and generated source must have an active membership owner.",
        },
        "summary": {
            "candidate_files": total_files,
            "candidate_source_bytes": total_bytes,
            "max_snapshot_bytes": int(policy["max_snapshot_bytes"]),
            "required_sources_missing": missing,
            "unclassified_source_assets": len(disposition_inventory["issues"]),
            "membership_projection_issues": len(membership_issues),
        },
    }


def source_specs(config: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    sources = {str(item.get("id") or ""): item for item in config.get("sources", []) if str(item.get("id") or "")}
    generated = {str(item.get("id") or ""): item for item in config.get("generated_sources", []) if str(item.get("id") or "")}
    return sources, generated


def source_dependency_graph(config: dict[str, Any]) -> dict[str, Any]:
    raw_ids = [str(item.get("id") or "") for item in [*config.get("sources", []), *config.get("generated_sources", [])]]
    sources, generated = source_specs(config)
    nodes = set(sources) | set(generated)
    issues: list[dict[str, Any]] = []
    duplicates = sorted({item for item in raw_ids if item and raw_ids.count(item) > 1})
    if duplicates:
        issues.append({"code": "source_id_duplicate", "source_ids": duplicates})
    graph: dict[str, list[str]] = {}
    for source_id, spec in [*sources.items(), *generated.items()]:
        dependencies = [str(item) for item in spec.get("depends_on", []) if str(item)]
        graph[source_id] = dependencies
        missing = sorted(set(dependencies) - nodes)
        if missing:
            issues.append({"code": "source_dependency_missing", "source_id": source_id, "missing": missing})
        watched_paths = spec.get("depends_on_paths")
        if watched_paths is None:
            continue
        if not isinstance(watched_paths, dict):
            issues.append({"code": "source_dependency_paths_invalid", "source_id": source_id})
            continue
        for dependency, patterns in watched_paths.items():
            dependency_id = str(dependency)
            if dependency_id not in dependencies:
                issues.append({"code": "source_dependency_path_without_dependency", "source_id": source_id, "dependency": dependency_id})
            elif not isinstance(patterns, list) or not any(str(pattern).strip() for pattern in patterns):
                issues.append({"code": "source_dependency_paths_empty", "source_id": source_id, "dependency": dependency_id})
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, chain: list[str]) -> None:
        if node in visiting:
            issues.append({"code": "source_dependency_cycle", "chain": chain + [node]})
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, []):
            if dependency in nodes:
                visit(dependency, chain + [node])
        visiting.remove(node)
        visited.add(node)

    for node in sorted(nodes):
        visit(node, [])
    reverse: dict[str, list[str]] = {node: [] for node in nodes}
    for node, dependencies in graph.items():
        for dependency in dependencies:
            if dependency in reverse:
                reverse[dependency].append(node)
    return {
        "ok": not issues,
        "issues": issues,
        "nodes": sorted(nodes),
        "graph": {key: sorted(value) for key, value in sorted(graph.items())},
        "reverse": {key: sorted(value) for key, value in sorted(reverse.items())},
    }


def path_matches_source(path: Path, source: Path, kind: str) -> bool:
    try:
        candidate = path.resolve()
        target = source.resolve()
    except OSError:
        candidate, target = path.absolute(), source.absolute()
    if kind == "file":
        return candidate == target
    return candidate == target or path_is_within(candidate, target)


def expand_changed_path(raw: str, variables: dict[str, str]) -> Path:
    value = str(raw or "").strip()
    logical_prefixes = {
        "codex_home:": "CODEX_HOME",
        "agent_home:": "AGENT_HOME",
        "cc_switch:": "CC_SWITCH_HOME",
        "workspace:": "WORKSPACE_ROOT",
    }
    lowered = value.lower()
    for prefix, variable in logical_prefixes.items():
        if lowered.startswith(prefix):
            suffix = value[len(prefix):].lstrip("/\\")
            return Path(variables[variable]) / Path(suffix)
    return Path(value).expanduser()


def source_path_for_current_host(value: str) -> Path:
    """Interpret manifest separators correctly in direct WSL repository tests."""
    return Path(value if os.name == "nt" else value.replace("\\", "/"))


def generated_dependency_is_affected(
    spec: dict[str, Any],
    dependency_id: str,
    source_file_changes: dict[str, set[str]],
) -> bool:
    """Apply optional path watches without weakening undeclared dependencies."""
    watched_paths = spec.get("depends_on_paths")
    if not isinstance(watched_paths, dict) or dependency_id not in watched_paths:
        return True
    changed_paths = source_file_changes.get(dependency_id)
    if not changed_paths:
        # A root-level source change has no safely narrow file set.
        return True
    patterns = [str(item).replace("\\", "/").lstrip("/") for item in watched_paths.get(dependency_id, []) if str(item).strip()]
    if not patterns:
        return True
    return any(
        fnmatch.fnmatchcase(changed.replace("\\", "/").lstrip("/").lower(), pattern.lower())
        for changed in changed_paths
        for pattern in patterns
    )


def affected_source_plan(config: dict[str, Any], changed_paths: list[str]) -> dict[str, Any]:
    variables = expanded_variables(config)
    sources, generated = source_specs(config)
    dependency = source_dependency_graph(config)
    original_changes = [str(item).strip() for item in changed_paths if str(item).strip()]
    normalized_changes = [str(expand_changed_path(item, variables)) for item in original_changes]
    direct_sources: set[str] = set()
    direct_generated: set[str] = set()
    source_file_changes: dict[str, set[str]] = {}
    unmatched: list[str] = []
    reasons: list[str] = []
    membership_ids = {MEMBERSHIP_ASSET_ID}
    for original, raw in zip(original_changes, normalized_changes):
        candidate = Path(raw)
        matched = False
        for source_id, spec in sources.items():
            source = source_path_for_current_host(expand_tokens(str(spec.get("source") or ""), variables))
            if source and path_matches_source(candidate, source, str(spec.get("kind") or "file")):
                direct_sources.add(source_id)
                if str(spec.get("kind") or "file") == "tree" and normalized_path(str(candidate)) != normalized_path(str(source)):
                    relative = relative_posix(candidate, source)
                    if relative not in {"", "."}:
                        source_file_changes.setdefault(source_id, set()).add(relative)
                matched = True
        for source_id, spec in generated.items():
            destination = normalized_path(str(spec.get("destination") or ""))
            if destination and normalized_path(original) == destination:
                direct_generated.add(source_id)
                matched = True
        if not matched:
            unmatched.append(raw)
    if unmatched:
        reasons.append("changed_path_unmapped")
    if not dependency["ok"]:
        reasons.append("dependency_graph_invalid")
    affected = set(direct_sources) | set(direct_generated)
    queue = list(affected)
    while queue:
        current = queue.pop(0)
        for dependent in dependency["reverse"].get(current, []):
            dependent_spec = generated.get(dependent)
            if dependent_spec and not generated_dependency_is_affected(dependent_spec, current, source_file_changes):
                continue
            if dependent not in affected:
                affected.add(dependent)
                queue.append(dependent)
    if direct_generated & membership_ids:
        reasons.append("membership_scope_changed")
    if not normalized_changes:
        reasons.append("changed_paths_required_for_incremental")
    full_rebuild = bool(reasons)
    guard_authority_refreshed = False
    if not full_rebuild and MEMBERSHIP_ASSET_ID in generated:
        # Snapshot exports intentionally remove retirement tombstones. Reusing
        # that sanitized export would erase the negative guard during the next
        # incremental build, so always reacquire the membership authority.
        affected.add(MEMBERSHIP_ASSET_ID)
        guard_authority_refreshed = True
    affected_sources = sorted(item for item in affected if item in sources)
    affected_generated = sorted(item for item in affected if item in generated)
    reused_sources = sorted(set(sources) - set(affected_sources)) if not full_rebuild else []
    reused_generated = sorted(set(generated) - set(affected_generated)) if not full_rebuild else []
    return {
        "schema": "codex_mirror.affected_source_plan.v1",
        "ok": dependency["ok"] and not unmatched and bool(normalized_changes),
        "changed_files": normalized_changes,
        "direct_source_ids": sorted(direct_sources),
        "source_file_changes": {key: sorted(value) for key, value in sorted(source_file_changes.items())},
        "direct_generated_source_ids": sorted(direct_generated),
        "dependent_generated_source_ids": affected_generated,
        "affected_source_ids": affected_sources,
        "reused_source_ids": reused_sources,
        "reused_generated_source_ids": reused_generated,
        "full_rebuild_required": full_rebuild,
        "guard_authority_refreshed": guard_authority_refreshed,
        "reasons": sorted(set(reasons)),
        "unmatched_paths": unmatched,
        "dependency_graph": {"ok": dependency["ok"], "issues": dependency["issues"]},
    }


def source_id_for_asset(asset_id: str) -> str:
    return str(asset_id).split(":", 1)[0]


def reuse_previous_asset_for_incremental(
    asset: dict[str, Any],
    affected_source_ids: set[str],
    affected_generated_ids: set[str],
    source_file_changes: dict[str, set[str]],
) -> bool:
    """Reuse a previous asset unless its source or generated owner requires recapture."""
    asset_id = str(asset.get("asset_id") or "")
    if asset_id in affected_generated_ids:
        return False
    source_id = source_id_for_asset(asset_id)
    if source_id not in affected_source_ids:
        return True
    changed_files = source_file_changes.get(source_id)
    if not changed_files or ":" not in asset_id:
        return False
    return asset_id.split(":", 1)[1] not in changed_files


def strip_volatile_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: strip_volatile_value(item) for key, item in value.items() if key not in {"generated_at", "created_at", "updated_at"}}
    if isinstance(value, list):
        return [strip_volatile_value(item) for item in value]
    return value


def normalized_snapshot_manifest(snapshot: str) -> dict[str, Any]:
    root = resolve_snapshot(snapshot)
    manifest = load_json(root / "snapshot-manifest.json")
    normalized = strip_volatile_value(manifest)
    normalized.pop("snapshot_id", None)
    normalized.pop("incremental", None)
    summary = normalized.get("summary")
    if isinstance(summary, dict):
        summary.pop("capture_mode", None)
    for asset in normalized.get("assets", []):
        if isinstance(asset, dict):
            asset.pop("reuse", None)
            content_kind = str(asset.get("content_kind") or "text")
            target = root / Path(str(asset.get("snapshot_path") or ""))
            if content_kind == "text" and target.suffix.lower() == ".json" and target.is_file():
                try:
                    payload = strip_volatile_value(load_json(target))
                    asset["sha256"] = sha256_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
    return normalized


def compare_snapshots(left: str, right: str) -> dict[str, Any]:
    try:
        first = normalized_snapshot_manifest(left)
        second = normalized_snapshot_manifest(right)
    except Exception as exc:
        return {"schema": "codex_mirror.snapshot_compare.v1", "ok": False, "reason": str(exc)}
    if first == second:
        return {"schema": "codex_mirror.snapshot_compare.v1", "ok": True, "equivalent": True, "left": left, "right": right, "differences": []}
    differences: list[dict[str, Any]] = []
    if first.get("dependency_graph") != second.get("dependency_graph"):
        differences.append({"field": "dependency_graph", "left": first.get("dependency_graph"), "right": second.get("dependency_graph")})
    left_assets = {str(item.get("asset_id")): item for item in first.get("assets", [])}
    right_assets = {str(item.get("asset_id")): item for item in second.get("assets", [])}
    for asset_id in sorted(set(left_assets) | set(right_assets)):
        if left_assets.get(asset_id) != right_assets.get(asset_id):
            differences.append({"asset_id": asset_id, "left": left_assets.get(asset_id), "right": right_assets.get(asset_id)})
    return {"schema": "codex_mirror.snapshot_compare.v1", "ok": True, "equivalent": False, "left": left, "right": right, "difference_count": len(differences), "differences": differences[:50]}


def copy_previous_asset(stage: Path, previous_root: Path, asset: dict[str, Any]) -> dict[str, Any]:
    source = previous_root / Path(str(asset["snapshot_path"]))
    if not source.is_file():
        raise FileNotFoundError(f"previous_asset_missing:{asset.get('asset_id')}")
    destination = stage / Path(str(asset["snapshot_path"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
        reuse_mode = "previous_snapshot_hardlink"
    except OSError:
        shutil.copy2(source, destination)
        reuse_mode = "previous_snapshot_copy"
    copied = dict(asset)
    copied.pop("reuse", None)
    copied["reuse"] = {
        "mode": reuse_mode,
        "snapshot_id": previous_root.name,
        "source_asset_id": str(asset.get("asset_id") or ""),
    }
    return copied


def previous_snapshot_for_incremental() -> tuple[Path | None, dict[str, Any] | None, str]:
    try:
        path = resolve_snapshot("latest")
        manifest = load_json(path / "snapshot-manifest.json")
        # Every reused file is checked for existence while linking/copying, and
        # the completed candidate receives the full hash and secret-scan
        # validation before promotion. Re-hashing the previous 2k-file
        # snapshot here duplicates that mandatory candidate check.
        if str(manifest.get("snapshot_id") or "") != path.name or not isinstance(manifest.get("assets"), list):
            return None, None, "previous_snapshot_invalid"
        return path, manifest, "previous_snapshot_valid"
    except Exception as exc:
        return None, None, f"previous_snapshot_unavailable:{type(exc).__name__}"


def create_snapshot(config: dict[str, Any], *, changed_paths: list[str] | None = None) -> dict[str, Any]:
    plan = collect_plan(config)
    if not plan["ok"]:
        return {"schema": "codex_mirror.snapshot.v1", "ok": False, "reason": "plan_blocked", "plan": plan}
    quiescence = capture_quiescence_probe(config)
    if not quiescence.get("ok"):
        return {
            "schema": "codex_mirror.snapshot.v1", "ok": False,
            "reason": str(quiescence.get("reason") or "capture_quiescence_probe_failed"),
            "candidate_created": False, "capture_quiescence": quiescence,
        }
    current_governance_hashes = governance_hashes()
    incremental_plan = None
    previous_root: Path | None = None
    previous_manifest: dict[str, Any] | None = None
    capture_mode = "full"
    fallback_reason = ""
    if changed_paths is not None:
        incremental_plan = affected_source_plan(config, changed_paths)
        if not incremental_plan["full_rebuild_required"] and incremental_plan["ok"]:
            previous_root, previous_manifest, previous_status = previous_snapshot_for_incremental()
            if previous_root is None or previous_manifest is None:
                incremental_plan["full_rebuild_required"] = True
                incremental_plan["reasons"] = sorted(set(incremental_plan["reasons"]) | {previous_status})
                fallback_reason = previous_status
            else:
                governance_reason = incremental_governance_change_reason(previous_manifest, current_governance_hashes)
                if governance_reason:
                    incremental_plan["full_rebuild_required"] = True
                    incremental_plan["reasons"] = sorted(set(incremental_plan["reasons"]) | {governance_reason})
                    fallback_reason = governance_reason
                else:
                    capture_mode = "incremental"
        else:
            fallback_reason = ",".join(incremental_plan["reasons"]) or "incremental_plan_not_safe"
    variables = expanded_variables(config)
    policy = config["policy"]
    seed = json.dumps({"time": now_iso(), "governance": current_governance_hashes}, sort_keys=True).encode("utf-8")
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + sha256_bytes(seed)[:10]
    staging_parent = RUNTIME_ROOT / "staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    stage = staging_parent / snapshot_id
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    assets: list[dict[str, Any]] = []
    missing: list[str] = []
    target = SNAPSHOT_ROOT / snapshot_id
    token = f"{os.getpid()}-{time.time_ns()}"
    transaction = {
        "schema": "codex_mirror.snapshot_transaction.v1",
        "token": token,
        "pid": os.getpid(),
        "started_at": now_iso(),
        "snapshot_id": snapshot_id,
        "stage_path": str(stage),
        "phase": "staging_created",
        "previous_latest": latest_snapshot_payload(),
    }
    promoted = False
    latest_updated = False
    try:
        write_snapshot_transaction(transaction)
        affected_ids = set(incremental_plan.get("affected_source_ids", [])) if capture_mode == "incremental" else set()
        affected_generated_ids = set(incremental_plan.get("dependent_generated_source_ids", [])) if capture_mode == "incremental" else set()
        if capture_mode == "incremental":
            # These host-owned sources are not represented in the Work Git
            # delta. Reusing their old snapshot payload guarantees a later
            # freshness failure whenever they changed since that snapshot.
            recapture_sources, recapture_generated = incremental_recapture_ids(config)
            affected_ids.update(recapture_sources)
            affected_generated_ids.update(recapture_generated)
        source_file_changes = {
            str(source_id): {str(path) for path in paths}
            for source_id, paths in (incremental_plan.get("source_file_changes") or {}).items()
            if isinstance(paths, list)
        } if capture_mode == "incremental" else {}
        if capture_mode == "incremental" and previous_manifest is not None and previous_root is not None:
            for asset in previous_manifest.get("assets", []):
                if reuse_previous_asset_for_incremental(asset, affected_ids, affected_generated_ids, source_file_changes):
                    assets.append(copy_previous_asset(stage, previous_root, asset))
        transaction["phase"] = "capturing_sources"
        write_snapshot_transaction(transaction)
        for spec in config.get("sources", []):
            if capture_mode == "incremental" and str(spec.get("id")) not in affected_ids:
                continue
            source = Path(expand_tokens(spec["source"], variables))
            if not source.exists():
                if spec.get("required"):
                    missing.append(spec["id"])
                continue
            if spec["kind"] == "file":
                content_kind = source_content_kind(source, spec)
                data, effective_mode, content_kind = source_payload(source, spec["mode"], content_kind)
                assets.append(add_asset(stage=stage, snapshot_path=spec["destination"], data=data, asset_id=spec["id"], owner=spec["owner"], classification=spec["classification"], source_path=str(source), restore_template=spec.get("restore_path", ""), mode=effective_mode, content_kind=content_kind))
                continue
            for path in iter_source_files(source, spec, policy):
                rel = relative_posix(path, source)
                if capture_mode == "incremental" and str(spec.get("id")) in source_file_changes and rel not in source_file_changes[str(spec.get("id"))]:
                    continue
                destination = str(Path(spec["destination"]) / Path(rel)).replace("\\", "/")
                restore_template = str(Path(spec.get("restore_path", "")) / Path(rel)).replace("/", "\\")
                content_kind = source_content_kind(path, spec)
                data, effective_mode, content_kind = source_payload(path, "copy", content_kind)
                assets.append(add_asset(stage=stage, snapshot_path=destination, data=data, asset_id=f"{spec['id']}:{rel}", owner=spec["owner"], classification=spec["classification"], source_path=str(path), restore_template=restore_template, mode=effective_mode, content_kind=content_kind))

        transaction["phase"] = "capturing_generated"
        write_snapshot_transaction(transaction)
        for spec in config.get("generated_sources", []):
            if capture_mode == "incremental" and str(spec.get("id")) not in affected_generated_ids:
                continue
            kind = spec["kind"]
            if kind == "command_json":
                data = run_json_command(spec["command"], variables)
            elif kind == "windows_tasks":
                data = export_windows_tasks(spec.get("patterns", []))
            elif kind == "windows_shortcuts":
                data = export_windows_shortcuts(spec.get("patterns", []))
            elif kind == "runtime_versions":
                data = export_runtime_versions(variables)
            elif kind == "plugin_inventory":
                data = export_plugin_inventory(
                    Path(expand_tokens(spec["config_source"], variables)),
                    Path(expand_tokens(spec["cache_root"], variables)),
                )
            elif kind == "current_checkpoints":
                data = export_current_checkpoints(
                    Path(expand_tokens(spec["manifest_source"], variables)),
                    Path(expand_tokens(spec["shared_root"], variables)),
                )
            else:
                raise ValueError(f"unsupported_generated_kind:{kind}")
            assets.append(add_asset(stage=stage, snapshot_path=spec["destination"], data=data, asset_id=spec["id"], owner=spec["owner"], classification=spec["classification"], mode=kind, content_kind="text"))

        if missing:
            raise ValueError("required_sources_missing:" + ",".join(missing))
        assets, membership_guard = apply_membership_guard(stage, assets)
        max_path_chars = int(policy.get("max_snapshot_relative_path_chars", 220))
        long_paths = [item["snapshot_path"] for item in assets if len(str(item["snapshot_path"])) > max_path_chars]
        if long_paths:
            raise ValueError("snapshot_relative_path_budget_exceeded:" + json.dumps(long_paths[:10], ensure_ascii=False))
        total_bytes = sum(int(item["bytes"]) for item in assets)
        if total_bytes > int(policy["max_snapshot_bytes"]):
            raise ValueError(f"snapshot_budget_exceeded:{total_bytes}")
        manifest = {
            "schema": "codex_mirror.snapshot_manifest.v1",
            "snapshot_id": snapshot_id,
            "created_at": now_iso(),
            "authority_mode": "derived_snapshot",
            "source_manifest": "manifests/source-authorities.json",
            "governance_hash_mode": GOVERNANCE_HASH_MODE,
            "governance_hashes": current_governance_hashes,
            "assets": assets,
            "membership_guard": membership_guard,
            "asset_dispositions": plan["asset_dispositions"],
            "external_archives": load_json(EXTERNAL_ARCHIVES),
            "summary": {"asset_count": len(assets), "total_bytes": total_bytes, "required_sources_missing": [], "capture_mode": capture_mode},
            "incremental": incremental_plan or {"capture_mode": "full"},
            "dependency_graph": source_dependency_graph(config),
        }
        write_json_atomic(stage / "snapshot-manifest.json", manifest)
        if target.exists():
            raise FileExistsError(target)
        transaction["phase"] = "ready_to_promote"
        write_snapshot_transaction(transaction)
        stage.replace(target)
        promoted = True
        transaction["phase"] = "promoted"
        write_snapshot_transaction(transaction)
        write_json_atomic(LATEST_PATH, {"schema": "codex_mirror.latest.v1", "snapshot_id": snapshot_id, "updated_at": now_iso()})
        latest_updated = True
        transaction["phase"] = "latest_updated"
        write_snapshot_transaction(transaction)
        clear_snapshot_transaction(token)
        return {"schema": "codex_mirror.snapshot.v1", "ok": True, "snapshot_id": snapshot_id, "path": str(target), "summary": manifest["summary"], "capture_mode": capture_mode, "fallback_reason": fallback_reason}
    except Exception as exc:
        if latest_updated:
            restore_latest_snapshot_payload(transaction["previous_latest"])
        if promoted:
            shutil.rmtree(target, ignore_errors=True)
        shutil.rmtree(stage, ignore_errors=True)
        clear_snapshot_transaction(token)
        return {"schema": "codex_mirror.snapshot.v1", "ok": False, "reason": str(exc), "snapshot_id": snapshot_id}


def snapshot_with_lock(config: dict[str, Any], *, changed_paths: list[str] | None = None) -> dict[str, Any]:
    lock_path = RUNTIME_ROOT / "locks" / "snapshot.lock"
    try:
        with exclusive_operation_lock(lock_path, "snapshot"):
            recovery = recover_interrupted_snapshot_state()
            if not recovery.get("ok"):
                return {
                    "schema": "codex_mirror.snapshot.v1",
                    "ok": False,
                    "reason": "snapshot_recovery_failed",
                    "recovery": recovery,
                }
            result = create_snapshot(config, changed_paths=changed_paths)
            if result.get("ok"):
                result["live_validation"] = validate_snapshot(
                    str(result.get("snapshot_id") or "latest"),
                    live_sources=True,
                    control_plane=False,
                )
            if isinstance(result, dict):
                result["interrupted_recovery"] = recovery
            return result
    except MirrorOperationBusy as exc:
        return {
            "schema": "codex_mirror.snapshot.v1",
            "ok": False,
            "reason": "mirror_operation_busy",
            "operation": "snapshot",
            "lock_path": str(exc.lock_path),
            "lock_owner": exc.owner,
        }


def resolve_snapshot(snapshot: str) -> Path:
    if snapshot and snapshot != "latest":
        path = SNAPSHOT_ROOT / snapshot
    else:
        if not LATEST_PATH.exists():
            raise FileNotFoundError("latest_snapshot_missing")
        path = SNAPSHOT_ROOT / str(load_json(LATEST_PATH)["snapshot_id"])
    if not path.is_dir():
        raise FileNotFoundError(f"snapshot_missing:{path.name}")
    return path


def restore_graph_issues() -> list[dict[str, Any]]:
    payload = load_json(RESTORE_ORDER)
    steps = payload.get("steps", [])
    ids = {str(item.get("id")) for item in steps}
    issues: list[dict[str, Any]] = []
    graph: dict[str, list[str]] = {}
    for item in steps:
        step_id = str(item.get("id") or "")
        dependencies = [str(value) for value in item.get("depends_on", [])]
        graph[step_id] = dependencies
        missing = [value for value in dependencies if value not in ids]
        if missing:
            issues.append({"code": "restore_dependency_missing", "step": step_id, "missing": missing})
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, chain: list[str]) -> None:
        if node in visiting:
            issues.append({"code": "restore_cycle", "chain": chain + [node]})
            return
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph.get(node, []):
            visit(dependency, chain + [node])
        visiting.discard(node)
        visited.add(node)

    for node in graph:
        visit(node, [])
    return issues


def agent_bootstrap_issues() -> list[dict[str, Any]]:
    path = ROOT / "AGENTS.md"
    if not path.is_file():
        return [{"code": "agent_bootstrap_missing", "path": "AGENTS.md"}]
    text = re.sub(r"\s+", " ", read_text(path)).lower()
    required_semantics = {
        "derived_authority_boundary": "derived, hashed recovery product",
        "validate_entry": "python scripts/mirror_cli.py validate",
        "isolated_stage": "empty isolated target",
        "activation_boundary": "staging never activates",
        "unknown_asset_guard": "unknown top-level source assets block refresh",
    }
    return [
        {"code": "agent_bootstrap_semantic_missing", "semantic": semantic, "path": "AGENTS.md"}
        for semantic, phrase in required_semantics.items()
        if phrase not in text
    ]


def repository_secret_findings() -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    roots = [ROOT / name for name in ("manifests", "scripts", "tests")]
    roots.extend(ROOT / name for name in ("AGENTS.md", "README.md", "BOOTSTRAP.md", "MIRROR_POLICY.md", "RESTORE.md", "SECURITY.md"))
    for root in roots:
        paths = [root] if root.is_file() else list(root.rglob("*"))
        for path in paths:
            if not path.is_file() or path.suffix.lower() in {".pyc"}:
                continue
            try:
                text = read_text(path)
            except ValueError:
                continue
            findings.extend(secret_findings(text, path=relative_posix(path, ROOT), config_file=path.suffix.lower() in CONFIG_EXTENSIONS))
    return findings


def wsl_location_from_unc(value: str) -> tuple[str, str] | None:
    raw = str(value or "").replace("/", "\\")
    matched = re.match(r"^\\\\(?:wsl\.localhost|wsl\$)\\([^\\]+)(\\.*)$", raw, flags=re.IGNORECASE)
    if not matched:
        return None
    return matched.group(1), "/" + matched.group(2).lstrip("\\").replace("\\", "/")


def current_work_git_release(config: dict[str, Any]) -> dict[str, Any]:
    authority = config.get("workspace_authority") if isinstance(config.get("workspace_authority"), dict) else {}
    variables = expanded_variables(config)
    location = wsl_location_from_unc(str(variables.get("WORK_GIT_ROOT") or authority.get("work_git_root") or ""))
    if location is None:
        return {}
    distribution, worktree = location
    user = str(authority.get("wsl_user") or "").strip()
    command = ["wsl.exe", "-d", distribution]
    if user:
        command.extend(["-u", user])
    command.extend([
        "--",
        "python3",
        f"{worktree}/workspace/_bridge/wsl_workspace_owner.py",
        "mirror-export",
        "--kind",
        "work-git-release",
    ])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
        if completed.returncode != 0:
            return {}
        payload = json.loads(completed.stdout.lstrip("\ufeff"))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def work_git_release_proves_snapshot_source(captured: dict[str, Any], current: dict[str, Any]) -> bool:
    captured_git = captured.get("work_git") if isinstance(captured.get("work_git"), dict) else {}
    current_git = current.get("work_git") if isinstance(current.get("work_git"), dict) else {}
    captured_head = str(captured_git.get("worktree_head") or "")
    current_head = str(current_git.get("worktree_head") or "")
    return bool(
        captured.get("ok")
        and current.get("ok")
        and captured_git.get("release_ready")
        and current_git.get("release_ready")
        and captured_git.get("clean")
        and current_git.get("clean")
        and captured_head
        and captured_head == str(captured_git.get("bare_head") or "")
        and captured_head == current_head
        and current_head == str(current_git.get("bare_head") or "")
    )


def source_ids_within_work_git(config: dict[str, Any]) -> set[str]:
    variables = expanded_variables(config)
    root = normalized_path(str(variables.get("WORK_GIT_ROOT") or "")).rstrip("/")
    if not root:
        return set()
    source_ids: set[str] = set()
    for spec in config.get("sources", []):
        source_id = str(spec.get("id") or "")
        source = normalized_path(expand_tokens(str(spec.get("source") or ""), variables))
        if source_id and (source == root or source.startswith(root + "/")):
            source_ids.add(source_id)
    return source_ids


def trusted_work_git_source_coverage(snapshot_root: Path, manifest: dict[str, Any], config: dict[str, Any]) -> tuple[set[str], dict[str, Any]]:
    asset = next((item for item in manifest.get("assets", []) if item.get("asset_id") == WORK_GIT_RELEASE_SOURCE_ID), None)
    if not isinstance(asset, dict):
        return set(), {"mode": "full_hash", "reason": "captured_work_git_release_receipt_missing"}
    try:
        captured = load_json(snapshot_root / Path(str(asset["snapshot_path"])))
    except (OSError, ValueError, json.JSONDecodeError):
        return set(), {"mode": "full_hash", "reason": "captured_work_git_release_receipt_unreadable"}
    current = current_work_git_release(config)
    if not work_git_release_proves_snapshot_source(captured, current):
        return set(), {"mode": "full_hash", "reason": "work_git_release_receipt_mismatch"}
    head = str((captured.get("work_git") or {}).get("worktree_head") or "")
    source_ids = source_ids_within_work_git(config)
    if not source_ids:
        return set(), {"mode": "full_hash", "reason": "work_git_source_roots_unresolved"}
    return source_ids, {"mode": "work_git_release_receipt", "head": head, "source_count": len(source_ids)}


def source_coverage_issues(manifest: dict[str, Any], *, trusted_source_ids: set[str] | None = None) -> list[dict[str, Any]]:
    config = load_json(SOURCE_MANIFEST)
    variables = expanded_variables(config)
    policy = config["policy"]
    actual_by_id = {str(item.get("asset_id")): item for item in manifest.get("assets", [])}
    actual = set(actual_by_id)
    guard = manifest.get("membership_guard", {})
    issues: list[dict[str, Any]] = []
    for spec in config.get("sources", []):
        if str(spec.get("id") or "") in (trusted_source_ids or set()):
            continue
        if spec.get("coverage_required", True) is False:
            continue
        source = Path(expand_tokens(spec["source"], variables))
        if not source.exists():
            continue
        expected_assets: dict[str, dict[str, str]] = {}
        if source.is_file():
            content_kind = source_content_kind(source, spec)
            data, _, _ = source_payload(source, spec["mode"], content_kind)
            expected_assets[str(spec["id"])] = {
                "sha256": sha256_bytes(data),
                "snapshot_path": str(spec.get("destination") or ""),
                "restore_template": str(spec.get("restore_path") or ""),
            }
        elif source.is_dir():
            for item in iter_source_files(source, spec, policy):
                rel = relative_posix(item, source)
                asset_id = f"{spec['id']}:{rel}"
                content_kind = source_content_kind(item, spec)
                data, _, _ = source_payload(item, "copy", content_kind)
                expected_assets[asset_id] = {
                    "sha256": sha256_bytes(data),
                    "snapshot_path": str(Path(spec.get("destination", "")) / Path(rel)).replace("\\", "/"),
                    "restore_template": str(Path(spec.get("restore_path", "")) / Path(rel)).replace("/", "\\"),
                }
        expected_assets = {
            asset_id: candidate
            for asset_id, candidate in expected_assets.items()
            if not guarded_asset_conflicts([{"asset_id": asset_id, **candidate}], guard)
        }
        expected = set(expected_assets)
        missing = sorted(expected - actual)
        stale = sorted(
            item
            for item in actual
            if (item == spec["id"] or item.startswith(str(spec["id"]) + ":")) and item not in expected
        )
        changed = sorted(
            asset_id
            for asset_id, expected_asset in expected_assets.items()
            if asset_id in actual_by_id
            and "inactive_member_sanitized" not in str(actual_by_id[asset_id].get("mode") or "")
            and str(actual_by_id[asset_id].get("sha256") or "") != expected_asset["sha256"]
        )
        if missing:
            issues.append({"code": "source_assets_missing", "source_id": spec["id"], "count": len(missing), "sample": missing[:10]})
        if stale:
            issues.append({"code": "source_assets_stale", "source_id": spec["id"], "count": len(stale), "sample": stale[:10]})
        if changed:
            issues.append({"code": "source_assets_changed", "source_id": spec["id"], "count": len(changed), "sample": changed[:10]})
    return issues


def strip_volatile_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: strip_volatile_fields(item) for key, item in value.items() if key not in {"generated_at"}}
    if isinstance(value, list):
        return [strip_volatile_fields(item) for item in value]
    return value


def generated_source_issues(snapshot_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    config = load_json(SOURCE_MANIFEST)
    variables = expanded_variables(config)
    actual_by_id = {str(item.get("asset_id")): item for item in manifest.get("assets", [])}
    issues: list[dict[str, Any]] = []
    for spec in config.get("generated_sources", []):
        asset_id = str(spec["id"])
        asset = actual_by_id.get(asset_id)
        if not asset:
            if spec.get("required"):
                issues.append({"code": "required_generated_asset_missing", "asset_id": asset_id})
            continue
        snapshot_payload_path = snapshot_root / Path(str(asset["snapshot_path"]))
        kind = str(spec["kind"])
        try:
            snapshot_payload = load_json(snapshot_payload_path)
            if kind == "plugin_inventory":
                config_source = Path(expand_tokens(spec["config_source"], variables))
                cache_root = Path(expand_tokens(spec["cache_root"], variables))
                if config_source.is_file() and cache_root.is_dir():
                    current = json.loads(export_plugin_inventory(config_source, cache_root))
                    if strip_volatile_fields(current) != strip_volatile_fields(snapshot_payload):
                        issues.append({"code": "generated_source_changed", "asset_id": asset_id, "source": "plugin_inventory"})
            elif kind == "current_checkpoints":
                manifest_source = Path(expand_tokens(spec["manifest_source"], variables))
                shared_root = Path(expand_tokens(spec["shared_root"], variables))
                if manifest_source.is_file() and shared_root.is_dir():
                    current = json.loads(export_current_checkpoints(manifest_source, shared_root))
                    if strip_volatile_fields(current) != strip_volatile_fields(snapshot_payload):
                        issues.append({"code": "generated_source_changed", "asset_id": asset_id, "source": "current_checkpoints"})
            elif kind == "runtime_versions":
                codex_home = Path(variables.get("CODEX_HOME", ""))
                if codex_home.is_dir():
                    current = json.loads(export_runtime_versions(variables))
                    if strip_volatile_fields(current) != strip_volatile_fields(snapshot_payload):
                        issues.append({"code": "generated_source_changed", "asset_id": asset_id, "source": "runtime_versions"})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            issues.append({"code": "generated_source_freshness_failed", "asset_id": asset_id, "detail": str(exc)})
    return issues


def validate_control_plane(snapshot: str = "latest") -> dict[str, Any]:
    try:
        path = resolve_snapshot(snapshot)
        manifest = load_json(path / "snapshot-manifest.json")
    except Exception as exc:
        return {"schema": "codex_mirror.control_plane_validate.v1", "ok": False, "issues": [{"code": "snapshot_load_failed", "detail": str(exc)}]}
    issues: list[dict[str, Any]] = []
    current_hashes = governance_hashes()
    for name, expected in manifest.get("governance_hashes", {}).items():
        if current_hashes.get(name) != expected:
            issues.append({"code": "governance_drift", "path": name, "snapshot_hash": expected, "current_hash": current_hashes.get(name, "missing")})
    issues.extend(control_plane_issues(str(manifest.get("snapshot_id") or "")))
    issues.extend(restore_graph_issues())
    issues.extend(agent_bootstrap_issues())
    issues.extend({"code": "repository_secret_detected", **finding} for finding in repository_secret_findings())
    return {
        "schema": "codex_mirror.control_plane_validate.v1",
        "ok": not issues,
        "snapshot_id": manifest.get("snapshot_id"),
        "mirror_valid": not issues,
        "capability_restore_ready": not issues,
        "full_state_restore_ready": False,
        "issues": issues,
    }


def validate_snapshot(
    snapshot: str = "latest",
    *,
    live_sources: bool = False,
    control_plane: bool = True,
    governance: bool = True,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    source_issues: list[dict[str, Any]] = []
    work_git_source_coverage = {"mode": "not_checked"}
    try:
        path = resolve_snapshot(snapshot)
        manifest = load_json(path / "snapshot-manifest.json")
    except Exception as exc:
        return {"schema": "codex_mirror.validate.v1", "ok": False, "issues": [{"code": "snapshot_load_failed", "detail": str(exc)}]}
    required_fields = {"schema", "snapshot_id", "created_at", "authority_mode", "governance_hash_mode", "assets", "membership_guard", "asset_dispositions", "summary", "dependency_graph"}
    missing_fields = sorted(required_fields - set(manifest))
    if missing_fields:
        issues.append({"code": "manifest_fields_missing", "fields": missing_fields})
    if manifest.get("authority_mode") != "derived_snapshot":
        issues.append({"code": "authority_mode_invalid", "observed": manifest.get("authority_mode")})
    if manifest.get("governance_hash_mode") != GOVERNANCE_HASH_MODE:
        issues.append({"code": "governance_hash_mode_invalid", "observed": manifest.get("governance_hash_mode"), "expected": GOVERNANCE_HASH_MODE})
    dependency = source_dependency_graph(load_json(SOURCE_MANIFEST))
    issues.extend(dependency.get("issues", []))
    asset_ids = [str(item.get("asset_id") or "") for item in manifest.get("assets", [])]
    duplicate_asset_ids = sorted({item for item in asset_ids if item and asset_ids.count(item) > 1})
    if duplicate_asset_ids:
        issues.append({"code": "snapshot_asset_id_duplicate", "asset_ids": duplicate_asset_ids[:20]})
    snapshot_dependency = manifest.get("dependency_graph")
    if not isinstance(snapshot_dependency, dict) or snapshot_dependency.get("ok") is not True:
        issues.append({"code": "snapshot_dependency_graph_missing_or_invalid"})
    elif snapshot_dependency.get("graph") != dependency.get("graph"):
        issues.append({"code": "snapshot_dependency_graph_drift"})
    guard = manifest.get("membership_guard", {})
    for asset in manifest.get("assets", []):
        relative = str(asset.get("snapshot_path") or "")
        if len(relative) > 220:
            issues.append({"code": "snapshot_relative_path_too_long", "path": relative, "chars": len(relative)})
        target = path / Path(relative)
        if not target.is_file():
            issues.append({"code": "asset_missing", "path": relative})
            continue
        observed_hash = sha256_file(target)
        if observed_hash != asset.get("sha256"):
            issues.append({"code": "asset_hash_mismatch", "path": relative, "expected": asset.get("sha256"), "observed": observed_hash})
        content_kind = str(asset.get("content_kind") or "text")
        if content_kind not in {"text", "binary"}:
            issues.append({"code": "asset_content_kind_invalid", "path": relative, "observed": content_kind})
            continue
        if content_kind == "binary":
            continue
        try:
            text = read_text(target)
            issues.extend({"code": "secret_detected", **finding} for finding in secret_findings(text, path=relative, config_file=target.suffix.lower() in CONFIG_EXTENSIONS))
            issues.extend(guarded_text_conflicts(text, guard, relative))
            if "inactive_member_sanitized" in str(asset.get("mode") or ""):
                try:
                    if target.suffix.lower() == ".py":
                        compile(text, relative, "exec")
                    elif target.suffix.lower() == ".json":
                        json.loads(text)
                    elif target.suffix.lower() == ".toml":
                        tomllib.loads(text)
                except (SyntaxError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
                    issues.append({"code": "sanitized_asset_parse_failed", "path": relative, "detail": str(exc)})
        except ValueError:
            issues.append({"code": "text_asset_decode_failed", "path": relative})
    if live_sources:
        source_config = load_json(SOURCE_MANIFEST)
        trusted_source_ids, work_git_source_coverage = trusted_work_git_source_coverage(path, manifest, source_config)
        source_issues.extend(membership_projection_issues(source_config))
        source_issues.extend(source_coverage_issues(manifest, trusted_source_ids=trusted_source_ids))
        source_issues.extend(generated_source_issues(path, manifest))
        source_issues.extend(collect_asset_dispositions(source_config)["issues"])
    asset_by_id = {str(item.get("asset_id")): item for item in manifest.get("assets", [])}
    for asset_id, checks in {
        "codex-plugin-inventory": (("unresolved_count", 0, "plugin_inventory_unresolved"),),
        "runtime-versions": (("codex_desktop.ok", True, "codex_desktop_version_missing"),),
        "mcp-bundle-readiness": (("bundle_plan_ready", True, "mcp_bundle_plan_not_ready"),),
    }.items():
        asset = asset_by_id.get(asset_id)
        if not asset:
            issues.append({"code": "required_generated_asset_missing", "asset_id": asset_id})
            continue
        try:
            payload = load_json(path / Path(str(asset["snapshot_path"])))
            for selector, expected, code in checks:
                value: Any = payload
                for part in selector.split("."):
                    value = value.get(part) if isinstance(value, dict) else None
                if value != expected:
                    issues.append({"code": code, "asset_id": asset_id, "observed": value, "expected": expected})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            issues.append({"code": "generated_asset_validation_failed", "asset_id": asset_id, "detail": str(exc)})
    if governance:
        current_hashes = governance_hashes()
        for name, expected in manifest.get("governance_hashes", {}).items():
            if current_hashes.get(name) != expected:
                issues.append({"code": "governance_drift", "path": name, "snapshot_hash": expected, "current_hash": current_hashes.get(name, "missing")})
    if control_plane:
        issues.extend(control_plane_issues(str(manifest.get("snapshot_id") or "")))
    issues.extend(restore_graph_issues())
    issues.extend(agent_bootstrap_issues())
    issues.extend({"code": "repository_secret_detected", **finding} for finding in repository_secret_findings())
    try:
        if guard.get("schema") != "codex_mirror.active_membership_guard.v1":
            issues.append({"code": "membership_guard_schema_invalid", "observed": guard.get("schema")})
        if guard.get("membership_export_sanitized") is not True:
            issues.append({"code": "membership_export_not_sanitized"})
        membership_asset = next(item for item in manifest.get("assets", []) if item.get("asset_id") == MEMBERSHIP_ASSET_ID)
        membership_payload = load_json(path / Path(str(membership_asset["snapshot_path"])))
        if "retirement_tombstones" in membership_payload or "retirement_tombstone_count" in membership_payload:
            issues.append({"code": "inactive_lifecycle_records_exported"})
        if membership_payload.get("mirror_export_scope") != "active_members_only":
            issues.append({"code": "membership_export_scope_invalid", "observed": membership_payload.get("mirror_export_scope")})
        issues.extend(guarded_asset_conflicts(manifest.get("assets", []), guard))
        issues.extend(guarded_registration_conflicts(path, manifest.get("assets", []), guard))
    except Exception as exc:
        issues.append({"code": "membership_guard_validation_failed", "detail": str(exc)})
    archive_assets = manifest.get("external_archives", {}).get("assets", [])
    required_archive_gaps = [item["asset_id"] for item in archive_assets if "missing" in str(item.get("status", "")) and not str(item.get("status", "")).startswith("optional")]
    git_dir = ROOT / ".git"
    remotes: list[str] = []
    if git_dir.exists():
        completed = subprocess.run([GIT_EXE, "-C", str(ROOT), "remote"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        if completed.returncode == 0:
            remotes = [line for line in completed.stdout.splitlines() if line.strip()]
    all_issues = [*issues, *source_issues]
    mirror_valid = not issues
    mcp_bundle_asset = asset_by_id.get("mcp-bundle-readiness")
    mcp_bundle_ready = False
    if mcp_bundle_asset:
        try:
            mcp_bundle_payload = load_json(path / Path(str(mcp_bundle_asset["snapshot_path"])))
            mcp_bundle_ready = mcp_bundle_payload.get("bundle_plan_ready") is True
        except (OSError, ValueError, json.JSONDecodeError):
            mcp_bundle_ready = False
    else:
        issues.append({"code": "required_generated_asset_missing", "asset_id": "mcp-bundle-readiness"})
    mirror_valid = not issues
    source_freshness_ok = not source_issues if live_sources else None
    return {
        "schema": "codex_mirror.validate.v1",
        "ok": not all_issues,
        "snapshot_id": manifest.get("snapshot_id"),
        "validation_scope": "snapshot_and_live_sources" if live_sources else "snapshot",
        "mirror_valid": mirror_valid,
        "capability_restore_ready": mirror_valid and mcp_bundle_ready,
        "source_freshness_checked": live_sources,
        "source_freshness_ok": source_freshness_ok,
        "full_state_restore_ready": mirror_valid and not required_archive_gaps and bool(remotes),
        "issues": all_issues,
        "advisories": {
            "required_archive_gaps": required_archive_gaps,
            "git_initialized": git_dir.exists(),
            "git_remotes": remotes,
            "remote_required_for_off_machine_recovery": True,
            "work_git_source_coverage": work_git_source_coverage,
        },
        "summary": manifest.get("summary", {}),
    }


def stage_relative_path(template: str, snapshot_path: str) -> Path:
    normalized = template.replace("/", "\\")
    mappings = {
        "${CODEX_HOME}": Path("codex-home"),
        "${AGENT_HOME}": Path("agent-home"),
        "${WORKSPACE_ROOT}": Path("workspace"),
        "${CC_SWITCH_HOME}": Path("cc-switch"),
        "${RESOURCE_LIBRARY}": Path("resource-library"),
    }
    for prefix, base in mappings.items():
        if normalized.upper().startswith(prefix.upper()):
            suffix = normalized[len(prefix):].lstrip("\\")
            return base / Path(suffix.replace("\\", "/"))
    return Path("derived") / Path(snapshot_path)


def restore_plan(snapshot: str, target_root: Path) -> dict[str, Any]:
    validation = validate_snapshot(snapshot)
    if not validation["ok"]:
        return {"schema": "codex_mirror.restore_plan.v1", "ok": False, "reason": "snapshot_invalid", "validation": validation}
    path = resolve_snapshot(snapshot)
    manifest = load_json(path / "snapshot-manifest.json")
    actions: list[dict[str, Any]] = []
    for asset in manifest.get("assets", []):
        relative = stage_relative_path(str(asset.get("restore_template") or ""), str(asset["snapshot_path"]))
        actions.append({
            "asset_id": asset["asset_id"],
            "source": str(path / Path(asset["snapshot_path"])),
            "stage_target": str(target_root / relative),
            "owner": asset["owner"],
            "classification": asset["classification"],
            "expected_sha256": asset["sha256"],
            "activation": "owner_required",
        })
    return {
        "schema": "codex_mirror.restore_plan.v1",
        "ok": True,
        "snapshot_id": manifest["snapshot_id"],
        "target_root": str(target_root),
        "action_count": len(actions),
        "actions": actions,
        "external_archive_gaps": validation["advisories"]["required_archive_gaps"],
        "rule": "This plan writes only to an isolated stage. Live activation is not included.",
    }


def is_same_or_child(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def stage_snapshot(snapshot: str, target_root: Path, confirm: str) -> dict[str, Any]:
    if confirm != "STAGE-RESTORE":
        return {"schema": "codex_mirror.stage.v1", "ok": False, "reason": "confirmation_required"}
    config = load_json(SOURCE_MANIFEST)
    variables = expanded_variables(config)
    target = target_root.resolve()
    active_roots = [Path(value).resolve() for key, value in variables.items() if key in {"CODEX_HOME", "WORKSPACE_ROOT", "CC_SWITCH_HOME", "RESOURCE_LIBRARY"}]
    if any(is_same_or_child(target, root) or is_same_or_child(root, target) for root in active_roots):
        return {"schema": "codex_mirror.stage.v1", "ok": False, "reason": "target_overlaps_active_source", "target": str(target)}
    if target.exists() and any(target.iterdir()):
        return {"schema": "codex_mirror.stage.v1", "ok": False, "reason": "target_must_be_empty", "target": str(target)}
    plan = restore_plan(snapshot, target)
    if not plan["ok"]:
        return {"schema": "codex_mirror.stage.v1", "ok": False, "reason": "restore_plan_failed", "plan": plan}
    target.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    try:
        for action in plan["actions"]:
            source = Path(action["source"])
            destination = Path(action["stage_target"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            observed_hash = sha256_file(destination)
            if observed_hash != action["expected_sha256"]:
                raise ValueError(f"stage_hash_mismatch:{action['asset_id']}")
            copied.append({"asset_id": action["asset_id"], "target": str(destination), "sha256": observed_hash, "hash_verified": True})
        receipt = {
            "schema": "codex_mirror.stage_receipt.v1",
            "ok": True,
            "snapshot_id": plan["snapshot_id"],
            "created_at": now_iso(),
            "target_root": str(target),
            "asset_count": len(copied),
            "assets": copied,
            "hashes_verified": all(item["hash_verified"] for item in copied),
            "external_archive_gaps": plan["external_archive_gaps"],
            "membership_guard": load_json(resolve_snapshot(snapshot) / "snapshot-manifest.json").get("membership_guard", {}),
            "activation_performed": False,
        }
        write_json_atomic(target / "stage-receipt.json", receipt)
        return {"schema": "codex_mirror.stage.v1", "ok": True, "receipt": str(target / "stage-receipt.json"), "summary": {"asset_count": len(copied), "target_root": str(target)}}
    except Exception as exc:
        return {"schema": "codex_mirror.stage.v1", "ok": False, "reason": str(exc), "copied_count": len(copied), "target": str(target)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex environment mirror and isolated recovery staging")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")
    affected_parser = sub.add_parser("affected-source-plan")
    affected_parser.add_argument("--changed", action="append", default=[])
    compare_parser = sub.add_parser("compare-snapshots")
    compare_parser.add_argument("--left", required=True)
    compare_parser.add_argument("--right", required=True)
    snapshot_parser = sub.add_parser("snapshot")
    snapshot_parser.add_argument("--apply", action="store_true")
    snapshot_parser.add_argument("--changed", action="append", default=None)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--snapshot", default="latest")
    validate_parser.add_argument("--live-sources", action="store_true")
    validate_parser.add_argument("--skip-control-plane", action="store_true", help=argparse.SUPPRESS)
    control_plane_parser = sub.add_parser("control-plane-validate")
    control_plane_parser.add_argument("--snapshot", default="latest")
    restore_parser = sub.add_parser("restore-plan")
    restore_parser.add_argument("--snapshot", default="latest")
    restore_parser.add_argument("--target-root", required=True)
    stage_parser = sub.add_parser("stage")
    stage_parser.add_argument("--snapshot", default="latest")
    stage_parser.add_argument("--target-root", required=True)
    stage_parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    config = load_json(SOURCE_MANIFEST)
    if args.command == "plan":
        payload = collect_plan(config)
    elif args.command == "affected-source-plan":
        payload = affected_source_plan(config, args.changed)
    elif args.command == "compare-snapshots":
        payload = compare_snapshots(args.left, args.right)
    elif args.command == "snapshot":
        payload = snapshot_with_lock(config, changed_paths=args.changed) if args.apply else {"schema": "codex_mirror.snapshot.v1", "ok": True, "dry_run": True, "plan": collect_plan(config), "next_action": "rerun with --apply"}
    elif args.command == "validate":
        payload = validate_snapshot(
            args.snapshot,
            live_sources=args.live_sources,
            control_plane=not args.skip_control_plane,
        )
    elif args.command == "control-plane-validate":
        payload = validate_control_plane(args.snapshot)
    elif args.command == "restore-plan":
        payload = restore_plan(args.snapshot, Path(args.target_root))
    else:
        payload = stage_snapshot(args.snapshot, Path(args.target_root), args.confirm)
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
