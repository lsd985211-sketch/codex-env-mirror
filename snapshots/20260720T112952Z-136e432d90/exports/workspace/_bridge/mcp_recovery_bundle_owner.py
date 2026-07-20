#!/usr/bin/env python3
"""Build and verify portable, content-addressed MCP recovery bundles.

The manifest is declarative source of truth. This owner never publishes an
archive, activates an MCP, or copies secrets, sessions, caches, or runtime
databases. It makes missing implementation bundles explicit so a fresh-agent
restore cannot be reported ready from source/configuration alone.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import posixpath
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "_bridge"
DEFAULT_MANIFEST = BRIDGE / "mcp_recovery_bundle_manifest.json"
DEFAULT_ARCHIVE_ROOT = Path.home() / ".codex-app" / "mcp-recovery-bundles"
SCHEMA = "mcp_recovery_bundle_owner.v1"
ARCHIVE_SCHEMA = "mcp_recovery_bundle_index.v1"
PUBLIC_DISTRIBUTIONS = {"github_release_asset", "github_release_asset_authorized_only"}
OFFLINE_IMPLEMENTATIONS = {"offline_node_bundle", "offline_python_bundle", "platform_binary"}
IMPLEMENTATIONS = OFFLINE_IMPLEMENTATIONS | {"source_tree", "remote_proxy_source", "plugin_reacquire"}
VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def host_platform() -> str:
    machine = platform.machine().lower()
    architecture = "x64" if machine in {"x86_64", "amd64"} else machine
    system = "windows" if sys.platform.startswith("win") else "linux" if sys.platform.startswith("linux") else sys.platform
    return f"{system}-{architecture}"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"manifest_unreadable:{type(exc).__name__}:{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("manifest_root_not_object")
    return value


def variables_for(manifest: dict[str, Any], overrides: dict[str, str] | None = None) -> dict[str, str]:
    values = {
        "WORK_GIT_ROOT": str(REPO_ROOT),
        "CODEX_RESOURCE_DEPENDENCIES": str(Path.home() / ".local" / "share" / "codex-resource-dependencies"),
        "WINDOWS_NATIVE_BRIDGE_ROOT": "/mnt/c/Users/45543/Downloads/mcsmanager_windows_release/mcsmanager/_bridge",
        "WINDOWS_USERPROFILE": "/mnt/c/Users/45543",
        **{key: str(value) for key, value in os.environ.items()},
    }
    values.update({str(key): str(value) for key, value in (manifest.get("variables") or {}).items()})
    values.update({str(key): str(value) for key, value in (overrides or {}).items()})
    for _ in range(8):
        changed = False
        for key, value in list(values.items()):
            expanded = VARIABLE.sub(lambda match: values.get(match.group(1), match.group(0)), value)
            if expanded != value:
                values[key] = expanded
                changed = True
        if not changed:
            break
    return values


def expand(value: str, variables: dict[str, str]) -> str:
    return VARIABLE.sub(lambda match: variables.get(match.group(1), match.group(0)), str(value or ""))


def safe_relative(value: str) -> bool:
    path = PurePosixPath(str(value).replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts and str(path) not in {"", "."}


def safe_tar_member(member: tarfile.TarInfo) -> tuple[bool, str]:
    if not safe_relative(member.name):
        return False, "archive_path_traversal"
    if member.isdev() or member.isfifo():
        return False, "archive_special_file"
    if member.issym() or member.islnk():
        target = PurePosixPath(member.linkname.replace("\\", "/"))
        resolved = posixpath.normpath(str(PurePosixPath(member.name).parent / target))
        if target.is_absolute() or resolved == ".." or resolved.startswith("../"):
            return False, "archive_link_escape"
    return True, ""


def bundle_by_id(manifest: dict[str, Any], bundle_id: str) -> dict[str, Any] | None:
    return next((item for item in manifest.get("bundles", []) if isinstance(item, dict) and item.get("id") == bundle_id), None)


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if manifest.get("schema") != "mcp_recovery_bundle_manifest.v1":
        issues.append({"code": "manifest_schema_invalid"})
    policy = manifest.get("policy") if isinstance(manifest.get("policy"), dict) else {}
    if policy.get("content_addressed") is not True or policy.get("hash_algorithm") != "sha256":
        issues.append({"code": "manifest_integrity_policy_invalid"})
    seen: set[str] = set()
    for bundle in manifest.get("bundles", []):
        if not isinstance(bundle, dict):
            issues.append({"code": "bundle_not_object"})
            continue
        bundle_id = str(bundle.get("id") or "")
        if not bundle_id or bundle_id in seen:
            issues.append({"code": "bundle_id_missing_or_duplicate", "bundle": bundle_id})
        seen.add(bundle_id)
        implementation = str(bundle.get("implementation_type") or "")
        if implementation not in IMPLEMENTATIONS:
            issues.append({"code": "bundle_implementation_invalid", "bundle": bundle_id})
        if not str(bundle.get("platform") or ""):
            issues.append({"code": "bundle_platform_missing", "bundle": bundle_id})
        for item in bundle.get("include", []):
            if not safe_relative(str(item)):
                issues.append({"code": "unsafe_include_pattern", "bundle": bundle_id, "value": str(item)})
        for item in bundle.get("entrypoints", []):
            if not safe_relative(str(item)):
                issues.append({"code": "unsafe_entrypoint", "bundle": bundle_id, "value": str(item)})
        distribution = str(bundle.get("distribution") or "")
        redistribution = bundle.get("redistribution") if isinstance(bundle.get("redistribution"), dict) else {}
        if distribution in PUBLIC_DISTRIBUTIONS:
            if redistribution.get("public_release") is not True or not str(redistribution.get("authorization") or ""):
                issues.append({"code": "public_distribution_authorization_missing", "bundle": bundle_id})
            if distribution == "github_release_asset_authorized_only" and not str(redistribution.get("authorization_ref") or ""):
                issues.append({"code": "explicit_distribution_authorization_reference_missing", "bundle": bundle_id})
    return {"schema": SCHEMA, "ok": not issues, "issues": issues, "bundle_count": len(seen)}


def resolve_source(bundle: dict[str, Any], variables: dict[str, str]) -> Path:
    return Path(expand(str(bundle.get("source") or ""), variables)).expanduser()


def matching_files(source: Path, patterns: list[Any]) -> list[Path]:
    files: set[Path] = set()
    for raw in patterns:
        pattern = str(raw)
        for path in source.glob(pattern):
            if path.is_file() or path.is_symlink():
                files.add(path)
    return sorted(files, key=lambda item: item.as_posix())


def source_inventory(bundle: dict[str, Any], variables: dict[str, str]) -> dict[str, Any]:
    source = resolve_source(bundle, variables)
    result = {"source": str(source), "source_exists": source.is_dir()}
    if not source.is_dir():
        return {**result, "ok": False, "reason": "bundle_source_missing", "files": [], "missing_entrypoints": list(bundle.get("entrypoints", []))}
    files = matching_files(source, list(bundle.get("include", [])))
    entries = [str(item) for item in bundle.get("entrypoints", [])]
    missing_entries = [item for item in entries if not (source / item).exists()]
    return {
        **result,
        "ok": bool(files) and not missing_entries,
        "reason": "" if files and not missing_entries else "bundle_source_incomplete",
        "files": files,
        "file_count": len(files),
        "missing_entrypoints": missing_entries,
    }


def index_path(archive_root: Path) -> Path:
    return archive_root / "index.json"


def load_index(archive_root: Path) -> dict[str, Any]:
    path = index_path(archive_root)
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"schema": ARCHIVE_SCHEMA, "bundles": {}}
    return value if isinstance(value, dict) and isinstance(value.get("bundles"), dict) else {"schema": ARCHIVE_SCHEMA, "bundles": {}}


def write_index(archive_root: Path, index: dict[str, Any]) -> Path:
    archive_root.mkdir(parents=True, exist_ok=True)
    target = index_path(archive_root)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return target


@contextmanager
def archive_lock(archive_root: Path):
    """Serialize index mutation so parallel bundle builds cannot lose entries."""
    archive_root.mkdir(parents=True, exist_ok=True)
    lock_path = archive_root / ".index.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def build_archive(bundle: dict[str, Any], variables: dict[str, str], archive_root: Path, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    inventory = source_inventory(bundle, variables)
    bundle_id = str(bundle.get("id") or "")
    if not inventory.get("ok"):
        return {"id": bundle_id, "ok": False, **inventory}
    source = Path(inventory["source"])
    file_hashes = {
        file_path.relative_to(source).as_posix(): sha256_file(file_path)
        for file_path in inventory["files"]
        if file_path.is_file() and not file_path.is_symlink()
    }
    fingerprint = hashlib.sha256(
        json.dumps(file_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if isinstance(existing, dict) and existing.get("source_fingerprint") == fingerprint:
        verified = verify_archive(existing, archive_root)
        if verified.get("ok"):
            return {**existing, "id": bundle_id, "ok": True, "reused": True, "source_fingerprint": fingerprint}
    archive_root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f"{bundle_id}-", suffix=".tar.gz", dir=archive_root, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with tarfile.open(temporary, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            for file_path in inventory["files"]:
                relative = file_path.relative_to(source).as_posix()
                if not safe_relative(relative):
                    return {"id": bundle_id, "ok": False, "reason": "unsafe_source_relative_path"}
                archive.add(file_path, arcname=relative, recursive=False)
        digest = sha256_file(temporary)
        final_path = archive_root / f"{bundle_id}-{digest[:16]}.tar.gz"
        os.replace(temporary, final_path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "id": bundle_id,
        "ok": True,
        "archive": final_path.name,
        "sha256": digest,
        "size_bytes": final_path.stat().st_size,
        "file_hashes": file_hashes,
        "source_fingerprint": fingerprint,
        "entrypoints": list(bundle.get("entrypoints", [])),
        "platform": bundle.get("platform"),
        "distribution": bundle.get("distribution"),
        "built_at": now_iso(),
    }


def verify_archive(entry: dict[str, Any], archive_root: Path) -> dict[str, Any]:
    archive_name = str(entry.get("archive") or "")
    archive = archive_root / archive_name
    if not safe_relative(archive_name) or not archive.is_file():
        return {"ok": False, "reason": "archive_missing", "archive": str(archive)}
    expected = str(entry.get("sha256") or "")
    observed = sha256_file(archive)
    if observed != expected:
        return {"ok": False, "reason": "archive_hash_mismatch", "expected": expected, "observed": observed}
    names: set[str] = set()
    try:
        with tarfile.open(archive, "r:gz") as content:
            for member in content.getmembers():
                safe, reason = safe_tar_member(member)
                if not safe:
                    return {"ok": False, "reason": reason, "member": member.name}
                names.add(member.name)
    except (OSError, tarfile.TarError) as exc:
        return {"ok": False, "reason": "archive_unreadable", "detail": f"{type(exc).__name__}:{exc}"}
    missing = [item for item in entry.get("entrypoints", []) if item not in names]
    return {"ok": not missing, "reason": "archive_entrypoint_missing" if missing else "", "archive": str(archive), "missing_entrypoints": missing, "member_count": len(names)}


def extract_verified(entry: dict[str, Any], archive_root: Path, target_root: Path) -> dict[str, Any]:
    verified = verify_archive(entry, archive_root)
    if not verified.get("ok"):
        return verified
    target = target_root.resolve()
    if target.exists() and any(target.iterdir()):
        return {"ok": False, "reason": "materialization_target_must_be_empty"}
    target.mkdir(parents=True, exist_ok=True)
    archive = archive_root / str(entry["archive"])
    try:
        with tarfile.open(archive, "r:gz") as content:
            for member in content.getmembers():
                safe, reason = safe_tar_member(member)
                if not safe:
                    return {"ok": False, "reason": reason, "member": member.name}
                destination = (target / member.name).resolve()
                if target not in destination.parents and destination != target:
                    return {"ok": False, "reason": "materialization_path_escape", "member": member.name}
            content.extractall(target)
    except (OSError, tarfile.TarError) as exc:
        return {"ok": False, "reason": "materialization_extract_failed", "detail": f"{type(exc).__name__}:{exc}"}
    observed: dict[str, str] = {}
    for relative, expected in (entry.get("file_hashes") or {}).items():
        path = target / relative
        if not path.is_file() or sha256_file(path) != expected:
            return {"ok": False, "reason": "materialized_file_hash_mismatch", "path": relative}
        observed[relative] = expected
    return {"ok": True, "target_root": str(target), "archive": str(archive), "entrypoints": list(entry.get("entrypoints", [])), "file_count": len(observed)}


def tools_list_smoke(entry: dict[str, Any], target_root: Path) -> dict[str, Any]:
    """Run a bounded initialize/tools/list smoke for known local MCP bundles."""
    bundle_id = str(entry.get("id") or "")
    if bundle_id == "mcp-node-filesystem-linux-x64":
        command = ["node", str(target_root / "node_modules/@modelcontextprotocol/server-filesystem/dist/index.js"), str(target_root)]
    elif bundle_id == "codegraph-linux-x64":
        try:
            result = subprocess.run(
                [str(target_root / "node_modules/@colbymchenry/codegraph-linux-x64/bin/codegraph"), "--help"],
                cwd=str(target_root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "bundle": bundle_id, "reason": "codegraph_cli_smoke_failed", "detail": f"{type(exc).__name__}:{exc}"}
        return {"ok": result.returncode == 0 and "Usage: codegraph" in result.stdout, "bundle": bundle_id, "smoke": "cli_help", "returncode": result.returncode}
    elif bundle_id == "gitnexus-linux-x64":
        command = [str(target_root / "node_modules/.bin/gitnexus"), "mcp"]
    elif bundle_id == "graphify-linux-x64":
        try:
            result = subprocess.run(
                [str(target_root / "node_modules/.bin/graphify"), "--help"],
                cwd=str(target_root), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "bundle": bundle_id, "reason": "graphify_cli_smoke_failed", "detail": f"{type(exc).__name__}:{exc}"}
        return {"ok": result.returncode == 0, "bundle": bundle_id, "smoke": "cli_help", "graph_regeneration_required": True, "returncode": result.returncode}
    else:
        return {"ok": False, "reason": "tools_list_smoke_not_defined", "bundle": bundle_id}
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "mcp-recovery-owner", "version": "1"}}}
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", cwd=str(target_root))
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps(initialize) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
        proc.stdin.flush()
        responses: list[dict[str, Any]] = []
        deadline = __import__("time").monotonic() + 20
        while __import__("time").monotonic() < deadline and len(responses) < 2:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("id") in {1, 2}:
                responses.append(payload)
        proc.terminate()
        tools = (next((item.get("result", {}).get("tools") for item in responses if item.get("id") == 2), None))
        return {"ok": bool(tools and isinstance(tools, list)), "bundle": bundle_id, "tool_count": len(tools or []), "tools": [item.get("name") for item in tools or []]}
    except (OSError, AssertionError, json.JSONDecodeError) as exc:
        return {"ok": False, "bundle": bundle_id, "reason": "tools_list_smoke_failed", "detail": f"{type(exc).__name__}:{exc}"}


def materialize(manifest: dict[str, Any], variables: dict[str, str], archive_root: Path, target_root: Path) -> dict[str, Any]:
    index = load_index(archive_root)
    statuses = readiness(manifest, variables, archive_root)
    if not statuses.get("bundle_plan_ready"):
        return {"schema": SCHEMA, "ok": False, "reason": "mcp_bundle_plan_not_ready", "readiness": statuses}
    receipts: list[dict[str, Any]] = []
    for bundle in manifest.get("bundles", []):
        bundle_id = str(bundle.get("id") or "")
        entry = (index.get("bundles") or {}).get(bundle_id)
        implementation = str(bundle.get("implementation_type") or "")
        if str(bundle.get("platform") or "") not in {host_platform(), "target-platform"}:
            receipts.append({"id": bundle_id, "status": "owner_receipt_required", "reason": "target_platform_owner_import_required", "platform": bundle.get("platform")})
            continue
        if implementation == "source_tree":
            inventory = source_inventory(bundle, variables)
            receipts.append({"id": bundle_id, "status": "source_restored" if inventory.get("ok") else "source_missing", "source": inventory.get("source"), "file_count": inventory.get("file_count", 0)})
            if not inventory.get("ok"):
                return {"schema": SCHEMA, "ok": False, "reason": "source_tree_materialization_failed", "bundle": bundle_id, "receipts": receipts}
            continue
        if not isinstance(entry, dict):
            if implementation in {"remote_proxy_source", "plugin_reacquire"} or bundle.get("distribution") == "encrypted_external_archive":
                receipts.append({"id": bundle_id, "status": "owner_receipt_required", "reason": "owner_reacquire_or_reconnect_required"})
                continue
            return {"schema": SCHEMA, "ok": False, "reason": "bundle_index_entry_missing", "bundle": bundle_id}
        result = extract_verified(entry, archive_root, target_root / bundle_id)
        if not result.get("ok"):
            return {"schema": SCHEMA, "ok": False, "reason": "bundle_materialization_failed", "bundle": bundle_id, "detail": result}
        smoke = tools_list_smoke(entry, Path(result["target_root"]))
        receipts.append({"id": bundle_id, "status": "verified" if smoke.get("ok") else "materialized_smoke_failed", "materialization": result, "tools_list": smoke})
        if not smoke.get("ok"):
            return {"schema": SCHEMA, "ok": False, "reason": "mcp_tools_list_smoke_failed", "bundle": bundle_id, "receipts": receipts}
    handoffs = [item for item in receipts if item.get("status") == "owner_receipt_required"]
    return {
        "schema": SCHEMA,
        "ok": not handoffs,
        "target_root": str(target_root),
        "receipts": receipts,
        "capability_restore_ready": not handoffs,
        "owner_handoffs_pending": [item.get("id") for item in handoffs],
        "next_action": "consume owner import/reacquire/reconnect receipts before claiming full capability restore" if handoffs else "fresh-agent MCP capability smoke complete",
    }


def bundle_status(bundle: dict[str, Any], variables: dict[str, str], index: dict[str, Any], archive_root: Path) -> dict[str, Any]:
    bundle_id = str(bundle.get("id") or "")
    implementation = str(bundle.get("implementation_type") or "")
    expected_platform = str(bundle.get("platform") or "")
    current_platform = host_platform()
    required = bool(bundle.get("required", True))
    status: dict[str, Any] = {"id": bundle_id, "required": required, "implementation_type": implementation, "platform": expected_platform, "source_restored": False, "offline_bundle_verified": False, "owner_reacquire_required": False, "remote_reconnect_required": False, "blocked_missing_bundle": False, "ready": False}
    if expected_platform not in {current_platform, "target-platform"}:
        status.update({"reason": "platform_mismatch", "owner_reacquire_required": implementation == "platform_binary"})
        return status
    if implementation == "plugin_reacquire":
        status.update({"source_restored": True, "owner_reacquire_required": True, "reason": "plugin_reacquire_required"})
        return status
    inventory = source_inventory(bundle, variables)
    if implementation in {"source_tree", "remote_proxy_source"}:
        status["source_restored"] = bool(inventory.get("ok"))
        if implementation == "remote_proxy_source":
            status["remote_reconnect_required"] = True
            status["reason"] = "remote_reconnect_required"
        else:
            status.update({"ready": bool(inventory.get("ok")), "reason": inventory.get("reason", "")})
        if not inventory.get("ok") and required:
            status["blocked_missing_bundle"] = True
        return {**status, "source": inventory.get("source"), "missing_entrypoints": inventory.get("missing_entrypoints", [])}
    if implementation in OFFLINE_IMPLEMENTATIONS:
        entry = (index.get("bundles") or {}).get(bundle_id) if isinstance(index.get("bundles"), dict) else None
        if not isinstance(entry, dict):
            status.update({"blocked_missing_bundle": required, "reason": "bundle_index_entry_missing"})
            return status
        verified = verify_archive(entry, archive_root)
        status["offline_bundle_verified"] = bool(verified.get("ok"))
        status["source_restored"] = bool(verified.get("ok"))
        status["ready"] = bool(verified.get("ok"))
        status["blocked_missing_bundle"] = required and not bool(verified.get("ok"))
        status["reason"] = verified.get("reason", "")
        status["archive"] = verified.get("archive", "")
        return status
    status.update({"blocked_missing_bundle": required, "reason": "unsupported_bundle_implementation"})
    return status


def readiness(manifest: dict[str, Any], variables: dict[str, str], archive_root: Path) -> dict[str, Any]:
    validated = validate_manifest(manifest)
    index = load_index(archive_root)
    statuses = [bundle_status(bundle, variables, index, archive_root) for bundle in manifest.get("bundles", []) if isinstance(bundle, dict)]
    blockers = [item for item in statuses if item.get("blocked_missing_bundle")]
    pending = [item for item in statuses if item.get("owner_reacquire_required") or item.get("remote_reconnect_required")]
    bundle_plan_ready = bool(validated.get("ok")) and not blockers
    ready = bool(validated.get("ok")) and not blockers and not pending and all(item.get("ready") for item in statuses if item.get("required"))
    return {
        "schema": SCHEMA,
        "ok": bool(validated.get("ok")),
        "capability_restore_ready": ready,
        "bundle_plan_ready": bundle_plan_ready,
        "manifest": validated,
        "archive_root": str(archive_root),
        "bundle_index": {
            "schema": index.get("schema", ARCHIVE_SCHEMA),
            "updated_at": index.get("updated_at", ""),
            "bundles": {
                str(bundle_id): {
                    key: entry.get(key)
                    for key in ("archive", "sha256", "size_bytes", "platform", "distribution", "entrypoints", "source_fingerprint")
                    if entry.get(key) not in (None, "")
                }
                for bundle_id, entry in (index.get("bundles") or {}).items()
                if isinstance(entry, dict)
            },
        },
        "statuses": statuses,
        "blocked_missing_bundle": [item["id"] for item in blockers],
        "owner_reacquire_required": [item["id"] for item in pending if item.get("owner_reacquire_required")],
        "remote_reconnect_required": [item["id"] for item in pending if item.get("remote_reconnect_required")],
        "next_action": "build approved public bundles, import encrypted private bundles, reacquire plugins, then reconnect remote MCPs" if not ready else "run isolated tools/list and representative tools/call smoke",
    }


def build(manifest: dict[str, Any], variables: dict[str, str], archive_root: Path, bundle_ids: list[str], include_nonpublic: bool, build_platform: str) -> dict[str, Any]:
    validated = validate_manifest(manifest)
    if not validated.get("ok"):
        return {"schema": SCHEMA, "ok": False, "reason": "manifest_invalid", "manifest": validated}
    selected = [bundle for bundle in manifest.get("bundles", []) if isinstance(bundle, dict) and (not bundle_ids or bundle.get("id") in bundle_ids)]
    with archive_lock(archive_root):
        index = load_index(archive_root)
        index["schema"] = ARCHIVE_SCHEMA
        index.setdefault("bundles", {})
        active_ids = {str(item.get("id")) for item in manifest.get("bundles", []) if isinstance(item, dict) and item.get("id")}
        index["bundles"] = {
            bundle_id: entry
            for bundle_id, entry in index["bundles"].items()
            if bundle_id in active_ids
        }
        results: list[dict[str, Any]] = []
        for bundle in selected:
            if bundle.get("implementation_type") not in OFFLINE_IMPLEMENTATIONS:
                results.append({"id": bundle.get("id"), "ok": True, "skipped": True, "reason": "implementation_not_archived"})
                continue
            if bundle.get("distribution") not in PUBLIC_DISTRIBUTIONS and not include_nonpublic:
                results.append({"id": bundle.get("id"), "ok": True, "skipped": True, "reason": "nonpublic_archive_requires_explicit_include"})
                continue
            if str(bundle.get("platform")) != build_platform:
                results.append({"id": bundle.get("id"), "ok": True, "skipped": True, "reason": "platform_deferred"})
                continue
            existing = index["bundles"].get(str(bundle.get("id"))) if isinstance(index["bundles"], dict) else None
            result = build_archive(bundle, variables, archive_root, existing)
            results.append(result)
            if result.get("ok"):
                index["bundles"][str(bundle.get("id"))] = result
        index["updated_at"] = now_iso()
        written = write_index(archive_root, index)
    compact = [
        {key: item.get(key) for key in ("id", "ok", "skipped", "reason", "archive", "sha256", "size_bytes", "reused") if item.get(key) not in (None, "")}
        for item in results
    ]
    return {"schema": SCHEMA, "ok": all(item.get("ok") for item in results), "archive_root": str(archive_root), "index": str(written), "results": compact}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="MCP recovery bundle owner")
    result.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    result.add_argument("--archive-root", default=str(DEFAULT_ARCHIVE_ROOT))
    result.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    sub = result.add_subparsers(dest="action", required=True)
    sub.add_parser("validate")
    sub.add_parser("readiness")
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--bundle", action="append", default=[])
    build_parser.add_argument("--include-nonpublic", action="store_true")
    build_parser.add_argument("--target-platform", default=host_platform())
    materialize_parser = sub.add_parser("materialize")
    materialize_parser.add_argument("--target-root", required=True)
    sub.add_parser("verify")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    overrides: dict[str, str] = {}
    for item in args.set:
        key, separator, value = str(item).partition("=")
        if not separator or not key:
            print(json.dumps({"schema": SCHEMA, "ok": False, "reason": "invalid_variable_override"}))
            return 1
        overrides[key] = value
    try:
        manifest = load_json(Path(args.manifest))
        variables = variables_for(manifest, overrides)
        archive_root = Path(args.archive_root).expanduser()
        if args.action == "validate":
            payload = {"schema": SCHEMA, **validate_manifest(manifest)}
        elif args.action == "readiness":
            payload = readiness(manifest, variables, archive_root)
        elif args.action == "build":
            payload = build(manifest, variables, archive_root, list(args.bundle), bool(args.include_nonpublic), str(args.target_platform))
        elif args.action == "materialize":
            payload = materialize(manifest, variables, archive_root, Path(args.target_root))
        else:
            index = load_index(archive_root)
            checks = {bundle_id: verify_archive(entry, archive_root) for bundle_id, entry in (index.get("bundles") or {}).items() if isinstance(entry, dict)}
            payload = {"schema": SCHEMA, "ok": all(item.get("ok") for item in checks.values()), "checks": checks, "archive_root": str(archive_root)}
    except ValueError as exc:
        payload = {"schema": SCHEMA, "ok": False, "reason": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
