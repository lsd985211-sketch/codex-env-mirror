#!/usr/bin/env python3
"""Standard-library mirror, validation, and isolated restore staging CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_ROOT = ROOT / "manifests"
SOURCE_MANIFEST = MANIFEST_ROOT / "source-authorities.json"
EXTERNAL_ARCHIVES = MANIFEST_ROOT / "external-archives.json"
RESTORE_ORDER = MANIFEST_ROOT / "restore-order.json"
SNAPSHOT_ROOT = ROOT / "snapshots"
RUNTIME_ROOT = ROOT / "runtime"
LATEST_PATH = SNAPSHOT_ROOT / "latest.json"

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
CC_SWITCH_SEMANTIC_TABLES = (
    "providers",
    "provider_endpoints",
    "mcp_servers",
    "prompts",
    "proxy_config",
    "settings",
    "skills",
    "skill_repos",
    "model_pricing",
)


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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    lines: list[str] = []
    assignment = re.compile(r"^(\s*)([A-Za-z0-9_.-]+)(\s*=\s*)(.*)$")
    for line in text.splitlines():
        match = assignment.match(line)
        if match and SENSITIVE_KEY.search(match.group(2)):
            secret_id = re.sub(r"[^A-Za-z0-9]+", "_", match.group(2)).upper().strip("_")
            line = f'{match.group(1)}{match.group(2)}{match.group(3)}"<SECRET:{secret_id}>"'
        line = INLINE_SENSITIVE_PATTERN.sub(lambda m: m.group(1) + '"<SECRET:REQUIRED>"', line)
        lines.append(scrub_known_tokens(line))
    return ("\n".join(lines) + "\n").encode("utf-8")


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
    completed = subprocess.run(expanded, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    if completed.returncode != 0:
        raise RuntimeError(f"command_failed:{expanded}:{completed.stderr[-2000:]}")
    payload = json.loads(completed.stdout.lstrip("\ufeff"))
    return (json.dumps(redact_json_value(payload), ensure_ascii=False, indent=2) + "\n").encode("utf-8")


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


def export_cc_switch_semantic(source: Path) -> bytes:
    if not source.is_file():
        raise FileNotFoundError(f"cc_switch_database_missing:{source}")
    connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        available = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = [name for name in CC_SWITCH_SEMANTIC_TABLES if name not in available]
        if missing:
            raise ValueError("cc_switch_semantic_tables_missing:" + ",".join(missing))
        tables: dict[str, Any] = {}
        for table in CC_SWITCH_SEMANTIC_TABLES:
            rows: list[dict[str, Any]] = []
            for raw in connection.execute(f'SELECT * FROM "{table}"'):
                row: dict[str, Any] = {}
                for key in raw.keys():
                    value = raw[key]
                    if SENSITIVE_KEY.search(str(key)):
                        row[str(key)] = "<SECRET:" + re.sub(r"[^A-Za-z0-9]+", "_", str(key)).upper().strip("_") + ">"
                    else:
                        row[str(key)] = redact_semantic_value(value)
                if table == "settings" and SENSITIVE_KEY.search(str(row.get("key") or "")):
                    row["value"] = "<SECRET:SETTING_VALUE>"
                rows.append(row)
            tables[table] = {"row_count": len(rows), "rows": rows}
        payload = {
            "schema": "codex_mirror.cc_switch_semantic_export.v1",
            "generated_at": now_iso(),
            "authority": "derived_from_cc_switch_database",
            "source_sha256": sha256_file(source),
            "excluded_state": [
                "request_logs",
                "usage_logs",
                "stream_logs",
                "health_state",
                "transient_backups",
                "raw_credentials",
            ],
            "tables": tables,
        }
        return json_bytes(payload)
    finally:
        connection.close()


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
        "git": ["git", "--version"],
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
        results[relative_posix(path, ROOT)] = sha256_file(path)
    for name in ("README.md", "BOOTSTRAP.md", "MIRROR_POLICY.md", "RESTORE.md", "SECURITY.md"):
        path = ROOT / name
        results[name] = sha256_file(path)
    for directory in (ROOT / "scripts", ROOT / "tests"):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix.lower() in {".py", ".ps1"}:
                results[relative_posix(path, ROOT)] = sha256_file(path)
    return results


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


def collect_plan(config: dict[str, Any]) -> dict[str, Any]:
    variables = expanded_variables(config)
    policy = config["policy"]
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
        "ok": not missing and total_bytes <= int(policy["max_snapshot_bytes"]),
        "generated_at": now_iso(),
        "sources": rows,
        "generated_sources": generated_rows,
        "summary": {
            "candidate_files": total_files,
            "candidate_source_bytes": total_bytes,
            "max_snapshot_bytes": int(policy["max_snapshot_bytes"]),
            "required_sources_missing": missing,
        },
    }


def create_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    plan = collect_plan(config)
    if not plan["ok"]:
        return {"schema": "codex_mirror.snapshot.v1", "ok": False, "reason": "plan_blocked", "plan": plan}
    variables = expanded_variables(config)
    policy = config["policy"]
    seed = json.dumps({"time": now_iso(), "governance": governance_hashes()}, sort_keys=True).encode("utf-8")
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + sha256_bytes(seed)[:10]
    staging_parent = RUNTIME_ROOT / "staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    stage = staging_parent / snapshot_id
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    assets: list[dict[str, Any]] = []
    missing: list[str] = []
    try:
        for spec in config.get("sources", []):
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
                destination = str(Path(spec["destination"]) / Path(rel)).replace("\\", "/")
                restore_template = str(Path(spec.get("restore_path", "")) / Path(rel)).replace("/", "\\")
                content_kind = source_content_kind(path, spec)
                data, effective_mode, content_kind = source_payload(path, "copy", content_kind)
                assets.append(add_asset(stage=stage, snapshot_path=destination, data=data, asset_id=f"{spec['id']}:{rel}", owner=spec["owner"], classification=spec["classification"], source_path=str(path), restore_template=restore_template, mode=effective_mode, content_kind=content_kind))

        for spec in config.get("generated_sources", []):
            kind = spec["kind"]
            if kind == "command_json":
                data = run_json_command(spec["command"], variables)
            elif kind == "windows_tasks":
                data = export_windows_tasks(spec.get("patterns", []))
            elif kind == "windows_shortcuts":
                data = export_windows_shortcuts(spec.get("patterns", []))
            elif kind == "runtime_versions":
                data = export_runtime_versions(variables)
            elif kind == "cc_switch_semantic_export":
                data = export_cc_switch_semantic(Path(expand_tokens(spec["source"], variables)))
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
            "governance_hashes": governance_hashes(),
            "assets": assets,
            "membership_guard": membership_guard,
            "external_archives": load_json(EXTERNAL_ARCHIVES),
            "summary": {"asset_count": len(assets), "total_bytes": total_bytes, "required_sources_missing": []},
        }
        write_json_atomic(stage / "snapshot-manifest.json", manifest)
        target = SNAPSHOT_ROOT / snapshot_id
        if target.exists():
            raise FileExistsError(target)
        stage.replace(target)
        write_json_atomic(LATEST_PATH, {"schema": "codex_mirror.latest.v1", "snapshot_id": snapshot_id, "updated_at": now_iso()})
        return {"schema": "codex_mirror.snapshot.v1", "ok": True, "snapshot_id": snapshot_id, "path": str(target), "summary": manifest["summary"]}
    except Exception as exc:
        shutil.rmtree(stage, ignore_errors=True)
        return {"schema": "codex_mirror.snapshot.v1", "ok": False, "reason": str(exc), "snapshot_id": snapshot_id}


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


def repository_secret_findings() -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    roots = [ROOT / name for name in ("manifests", "scripts", "tests")]
    roots.extend(ROOT / name for name in ("README.md", "BOOTSTRAP.md", "MIRROR_POLICY.md", "RESTORE.md", "SECURITY.md"))
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


def source_coverage_issues(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    config = load_json(SOURCE_MANIFEST)
    variables = expanded_variables(config)
    policy = config["policy"]
    actual = {str(item.get("asset_id")) for item in manifest.get("assets", [])}
    issues: list[dict[str, Any]] = []
    for spec in config.get("sources", []):
        if not spec.get("coverage_required"):
            continue
        source = Path(expand_tokens(spec["source"], variables))
        expected: set[str] = set()
        if source.is_file():
            expected.add(str(spec["id"]))
        elif source.is_dir():
            expected.update(
                f"{spec['id']}:{relative_posix(path, source)}"
                for path in iter_source_files(source, spec, policy)
            )
        missing = sorted(expected - actual)
        stale = sorted(
            item
            for item in actual
            if (item == spec["id"] or item.startswith(str(spec["id"]) + ":")) and item not in expected
        )
        if missing:
            issues.append({"code": "source_assets_missing", "source_id": spec["id"], "count": len(missing), "sample": missing[:10]})
        if stale:
            issues.append({"code": "source_assets_stale", "source_id": spec["id"], "count": len(stale), "sample": stale[:10]})
    return issues


def validate_snapshot(snapshot: str = "latest") -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    try:
        path = resolve_snapshot(snapshot)
        manifest = load_json(path / "snapshot-manifest.json")
    except Exception as exc:
        return {"schema": "codex_mirror.validate.v1", "ok": False, "issues": [{"code": "snapshot_load_failed", "detail": str(exc)}]}
    required_fields = {"schema", "snapshot_id", "created_at", "authority_mode", "assets", "membership_guard", "summary"}
    missing_fields = sorted(required_fields - set(manifest))
    if missing_fields:
        issues.append({"code": "manifest_fields_missing", "fields": missing_fields})
    if manifest.get("authority_mode") != "derived_snapshot":
        issues.append({"code": "authority_mode_invalid", "observed": manifest.get("authority_mode")})
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
    issues.extend(source_coverage_issues(manifest))
    asset_by_id = {str(item.get("asset_id")): item for item in manifest.get("assets", [])}
    for asset_id, checks in {
        "codex-plugin-inventory": (("unresolved_count", 0, "plugin_inventory_unresolved"),),
        "runtime-versions": (("codex_desktop.ok", True, "codex_desktop_version_missing"),),
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
    current_hashes = governance_hashes()
    for name, expected in manifest.get("governance_hashes", {}).items():
        if current_hashes.get(name) != expected:
            issues.append({"code": "governance_drift", "path": name, "snapshot_hash": expected, "current_hash": current_hashes.get(name, "missing")})
    issues.extend(restore_graph_issues())
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
        completed = subprocess.run(["git", "-C", str(ROOT), "remote"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        if completed.returncode == 0:
            remotes = [line for line in completed.stdout.splitlines() if line.strip()]
    return {
        "schema": "codex_mirror.validate.v1",
        "ok": not issues,
        "snapshot_id": manifest.get("snapshot_id"),
        "mirror_valid": not issues,
        "capability_restore_ready": not issues,
        "full_state_restore_ready": not issues and not required_archive_gaps and bool(remotes),
        "issues": issues,
        "advisories": {
            "required_archive_gaps": required_archive_gaps,
            "git_initialized": git_dir.exists(),
            "git_remotes": remotes,
            "remote_required_for_off_machine_recovery": True,
        },
        "summary": manifest.get("summary", {}),
    }


def stage_relative_path(template: str, snapshot_path: str) -> Path:
    normalized = template.replace("/", "\\")
    mappings = {
        "${CODEX_HOME}": Path("codex-home"),
        "${WORKSPACE_ROOT}": Path("workspace"),
        "${CC_SWITCH_HOME}": Path("cc-switch"),
        "${RESOURCE_LIBRARY}": Path("resource-library"),
    }
    for prefix, base in mappings.items():
        if normalized.upper().startswith(prefix.upper()):
            suffix = normalized[len(prefix):].lstrip("\\")
            return base / Path(suffix)
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
    snapshot_parser = sub.add_parser("snapshot")
    snapshot_parser.add_argument("--apply", action="store_true")
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--snapshot", default="latest")
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
    elif args.command == "snapshot":
        payload = create_snapshot(config) if args.apply else {"schema": "codex_mirror.snapshot.v1", "ok": True, "dry_run": True, "plan": collect_plan(config), "next_action": "rerun with --apply"}
    elif args.command == "validate":
        payload = validate_snapshot(args.snapshot)
    elif args.command == "restore-plan":
        payload = restore_plan(args.snapshot, Path(args.target_root))
    else:
        payload = stage_snapshot(args.snapshot, Path(args.target_root), args.confirm)
    print_json(payload)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
