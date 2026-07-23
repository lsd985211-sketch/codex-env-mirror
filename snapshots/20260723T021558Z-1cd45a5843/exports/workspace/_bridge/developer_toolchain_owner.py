#!/usr/bin/env python3
"""Version-locked WSL developer toolchain lifecycle owner.

The owner consumes resource-layer package/download operations, projects only
managed executables into the user PATH, and never performs system-wide installs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BRIDGE_ROOT = Path(__file__).resolve().parent
LOCK_PATH = BRIDGE_ROOT / "policies" / "developer_toolchain.lock.json"
RESOURCE_ENTRY = BRIDGE_ROOT / "codex_workflow_entry.py"
INSTALL_CONFIRM = "INSTALL-DEVELOPER-TOOLCHAIN"
APP_SERVER_PATH_BIN_DIR = Path("/mnt/c/Users/45543/.local/bin")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_lock(path: Path = LOCK_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "developer_toolchain.lock.v1":
        raise ValueError("developer_toolchain_lock_schema_invalid")
    components = payload.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("developer_toolchain_components_missing")
    return payload


def desired_executable_contracts(lock: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    payload = lock or load_lock()
    result: dict[str, dict[str, Any]] = {}
    for component in payload["components"]:
        if not isinstance(component, dict):
            continue
        for executable in component.get("executables", []):
            if not isinstance(executable, dict) or not executable.get("name"):
                continue
            name = str(executable["name"])
            result[name] = {
                **executable,
                "component": str(component.get("id") or ""),
                "provider": str(component.get("provider") or ""),
                "required": bool(component.get("required")),
                "target_subdir": str(component.get("target_subdir") or ""),
                "source": str(component.get("source") or ""),
            }
    return result


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _run(argv: list[str], *, timeout: int = 300) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def _json_result(argv: list[str], *, timeout: int = 300) -> dict[str, Any]:
    result = _run(argv, timeout=timeout)
    try:
        payload = json.loads(result["stdout"])
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return {
        "ok": bool(result.get("ok") and isinstance(payload, dict) and payload.get("ok")),
        "payload": payload,
        "returncode": result.get("returncode"),
        "stderr": str(result.get("stderr") or "")[-1200:],
    }


def _expected_path(component: dict[str, Any], executable: dict[str, Any], install_root: Path) -> Path | None:
    if component.get("provider") == "codex_runtime":
        return None
    return install_root / str(component.get("target_subdir") or "") / str(executable.get("relative_path") or "")


def _archive_source_path(target: Path, executable: dict[str, Any]) -> Path:
    relative = str(executable.get("source_relative_path") or executable.get("relative_path") or "")
    return target / relative


def _write_credential_bridge_wrapper(target: Path, executable: dict[str, Any]) -> dict[str, Any]:
    bridge = executable.get("credential_bridge")
    if not isinstance(bridge, dict):
        return {"ok": True, "changed": False, "reason": "not_required"}
    if bridge.get("type") != "windows_gh_token":
        return {"ok": False, "reason": "unsupported_credential_bridge"}
    native = _archive_source_path(target, executable)
    wrapper = target / str(executable.get("relative_path") or "")
    if not native.is_file() or not _inside(wrapper, target):
        return {"ok": False, "reason": "credential_bridge_source_missing", "native": str(native)}
    host_command = str(bridge.get("host_command") or "gh.exe")
    host_override = str(bridge.get("host_override_env") or "CODEX_WINDOWS_GH_PATH")
    content = f'''#!/usr/bin/env python3
"""Ephemeral WSL-to-Windows GitHub CLI credential bridge."""
import os
import shutil
import subprocess
import sys

native = {str(native)!r}
environment = os.environ.copy()
needs_token = not environment.get("GH_TOKEN") and not environment.get("GITHUB_TOKEN")
if needs_token and sys.argv[1:] != ["--version"]:
    host = environment.get({host_override!r}) or shutil.which({host_command!r})
    if host:
        result = subprocess.run(
            [host, "auth", "token"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        token = result.stdout.strip() if result.returncode == 0 else ""
        if token:
            environment["GH_TOKEN"] = token
os.execve(native, [native, *sys.argv[1:]], environment)
'''
    if wrapper.is_file() and wrapper.read_text(encoding="utf-8") == content:
        wrapper.chmod(0o755)
        return {"ok": True, "changed": False, "wrapper": str(wrapper), "native": str(native)}
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    temporary = wrapper.with_name(f".{wrapper.name}.credential-bridge-{os.getpid()}")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o755)
    os.replace(temporary, wrapper)
    return {"ok": True, "changed": True, "wrapper": str(wrapper), "native": str(native)}


def _probe_version(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    if not path:
        return {"ok": False, "path": "", "version": "", "error": "not_on_path"}
    result = _run([name, "--version"], timeout=15)
    output = str(result.get("stdout") or result.get("stderr") or "").strip().splitlines()
    return {
        "ok": bool(result.get("ok")),
        "path": path,
        "version": output[0] if output else "",
        "error": "" if result.get("ok") else str(result.get("stderr") or "")[-400:],
    }


def snapshot(
    lock: dict[str, Any] | None = None,
    *,
    install_root: Path | None = None,
    bin_dir: Path | None = None,
) -> dict[str, Any]:
    payload = lock or load_lock()
    managed_root = install_root or _path(str(payload["install_root"]))
    projection_root = bin_dir or _path(str(payload["bin_dir"]))
    tools: list[dict[str, Any]] = []
    for component in payload["components"]:
        if not isinstance(component, dict):
            continue
        for executable in component.get("executables", []):
            if not isinstance(executable, dict):
                continue
            name = str(executable.get("name") or "")
            probe = _probe_version(name)
            expected = _expected_path(component, executable, managed_root)
            source_path = _archive_source_path(
                managed_root / str(component.get("target_subdir") or ""), executable
            ) if executable.get("source_relative_path") else None
            expected_prefix = str(executable.get("version_prefix") or "")
            version_ok = bool(probe.get("ok") and str(probe.get("version") or "").startswith(expected_prefix))
            projection_ok = True
            target_exists = True
            if expected is not None:
                target_exists = expected.is_file()
                projected = projection_root / name
                projection_ok = projected.is_symlink() and projected.resolve() == expected.resolve()
            source_exists = source_path.is_file() if source_path is not None else True
            bridge = executable.get("credential_bridge") if isinstance(executable.get("credential_bridge"), dict) else {}
            host_command = str(bridge.get("host_command") or "")
            host_path = shutil.which(host_command) if host_command else ""
            appserver_projection: dict[str, Any] = {}
            appserver_projection_ok = True
            if name == "gh" and expected is not None:
                shim = APP_SERVER_PATH_BIN_DIR / name
                appserver_projection = {"path": str(shim), "target": str(expected)}
                try:
                    appserver_projection_ok = shim.is_file() and shim.read_text(encoding="utf-8") == _appserver_path_shim_content(expected)
                except OSError:
                    appserver_projection_ok = False
                appserver_projection["ok"] = appserver_projection_ok
            row_ok = bool(version_ok and target_exists and projection_ok and source_exists and appserver_projection_ok)
            tools.append({
                "name": name,
                "component": str(component.get("id") or ""),
                "provider": str(component.get("provider") or ""),
                "required": bool(component.get("required")),
                "ok": row_ok,
                "path": str(probe.get("path") or ""),
                "version": str(probe.get("version") or ""),
                "expected_version_prefix": expected_prefix,
                "expected_path": str(expected or ""),
                "target_exists": target_exists,
                "source_path": str(source_path or ""),
                "source_exists": source_exists,
                "projection_ok": projection_ok,
                "credential_bridge": str(bridge.get("type") or ""),
                "credential_host_path": str(host_path or ""),
                "appserver_path_projection": appserver_projection,
                "source": str(component.get("source") or ""),
                "use": str(executable.get("use") or ""),
                "error": str(probe.get("error") or ""),
            })
    missing = [row["name"] for row in tools if row["required"] and not row["ok"]]
    return {
        "schema": "developer_toolchain_owner.snapshot.v1",
        "ok": not missing,
        "generated_at": now_iso(),
        "lock_path": str(LOCK_PATH),
        "install_root": str(managed_root),
        "bin_dir": str(projection_root),
        "tools": tools,
        "missing_required": missing,
        "system_install_used": False,
    }


def plan() -> dict[str, Any]:
    state = snapshot()
    return {
        "schema": "developer_toolchain_owner.plan.v1",
        "ok": True,
        "generated_at": now_iso(),
        "changes_required": not state["ok"],
        "missing_required": state["missing_required"],
        "steps": [
            "acquire pinned Python packages through the resource-layer package owner",
            "acquire hash-locked release archives through resource materialization",
            "reject unsafe archives and foreign PATH projections",
            "atomically project managed executables into the user-local bin directory",
            "validate exact versions and managed targets without deleting prior versions",
        ],
        "confirmation": INSTALL_CONFIRM,
        "rollback": "retain old version roots; repoint only managed symlinks after validation",
        "snapshot": state,
    }


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _project_executable(target: Path, link: Path, install_root: Path) -> dict[str, Any]:
    if not target.is_file():
        return {"ok": False, "reason": "managed_executable_missing", "target": str(target)}
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() and link.resolve() == target.resolve():
        return {"ok": True, "changed": False, "link": str(link), "target": str(target)}
    if link.exists() or link.is_symlink():
        if not link.is_symlink() or not _inside(link.resolve(), install_root):
            return {"ok": False, "reason": "foreign_path_projection_refused", "link": str(link)}
    temporary = link.with_name(f".{link.name}.toolchain-{os.getpid()}")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, link)
    return {"ok": True, "changed": True, "link": str(link), "target": str(target)}


def _appserver_path_shim_content(target: Path) -> str:
    return (
        "#!/bin/sh\n"
        "# Managed by developer_toolchain_owner; do not edit.\n"
        f"exec {shlex.quote(str(target))} \"$@\"\n"
    )


def _project_appserver_path_shim(target: Path, directory: Path = APP_SERVER_PATH_BIN_DIR) -> dict[str, Any]:
    """Expose a managed WSL CLI through the Windows-derived app-server PATH."""
    if not target.is_file():
        return {"ok": False, "reason": "managed_executable_missing", "target": str(target)}
    shim = directory / target.name
    content = _appserver_path_shim_content(target)
    if shim.is_file():
        try:
            if shim.read_text(encoding="utf-8") == content:
                shim.chmod(0o755)
                return {"ok": True, "changed": False, "shim": str(shim), "target": str(target)}
        except OSError:
            pass
    if shim.exists() or shim.is_symlink():
        return {"ok": False, "reason": "foreign_appserver_path_projection_refused", "shim": str(shim)}
    directory.mkdir(parents=True, exist_ok=True)
    temporary = shim.with_name(f".{shim.name}.toolchain-{os.getpid()}")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.chmod(0o755)
    os.replace(temporary, shim)
    return {"ok": True, "changed": True, "shim": str(shim), "target": str(target)}


def _safe_extract_tar(archive: Path, destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    extracted = 0
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        for member in members:
            target = destination / member.name
            if member.name.startswith("/") or not _inside(target, destination):
                return {"ok": False, "reason": "archive_path_escape", "member": member.name}
            if member.issym() or member.islnk() or member.isdev():
                return {"ok": False, "reason": "archive_special_member_refused", "member": member.name}
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = handle.extractfile(member)
            if source is None:
                return {"ok": False, "reason": "archive_member_unreadable", "member": member.name}
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)
            extracted += 1
    return {"ok": True, "extracted_file_count": extracted}


def _resource_paths(lock: dict[str, Any]) -> dict[str, Path]:
    root = _path(str(lock["resource_runtime_root"]))
    return {
        "root": root,
        "store": root / "resources",
        "events": root / "logs" / "resource-broker-events.jsonl",
        "receipts": root / "logs" / "resource-broker-receipts.jsonl",
        "resource_log": root / "logs" / "resource-fetcher.jsonl",
    }


def _install_python_component(component: dict[str, Any], target: Path, paths: dict[str, Path]) -> dict[str, Any]:
    if target.exists() and any(target.iterdir()):
        return {"ok": False, "reason": "partial_or_unmanaged_target_refused", "target": str(target)}
    command = [
        sys.executable, str(RESOURCE_ENTRY), "resource", "job", "run",
        "--task", f"Install pinned {component['id']} for WSL developer toolchain",
        "--target", str(component["package_spec"]),
        "--intent", "package_dependency", "--auto-owner",
        "--package-ecosystem", "python", "--package-action", "install",
        "--package-id", str(component["id"]), "--resource-kind", "package",
        "--source-mode", "official_first", "--authority", "official",
        "--freshness", "versioned", "--validation-profile", "live",
        "--allow-network", "--allow-filesystem-write", "--install-approved",
        "--target-dir", str(target), "--destination-policy", "explicit_target_dir",
        "--store-root", str(paths["store"]), "--event-log", str(paths["events"]),
        "--receipt-log", str(paths["receipts"]), "--resource-log", str(paths["resource_log"]),
        "--receipt-detail", "compact", "--timeout", "180", "--json",
    ]
    result = _json_result(command, timeout=240)
    return {**result, "component": component["id"], "target": str(target)}


def _find_hash_locked_archive(download_dir: Path, expected_hash: str) -> Path | None:
    for candidate in sorted(download_dir.glob("*")) if download_dir.is_dir() else []:
        if candidate.is_file() and sha256_file(candidate) == expected_hash:
            return candidate
    return None


def _install_archive_component(component: dict[str, Any], target: Path, paths: dict[str, Path]) -> dict[str, Any]:
    expected_hash = str(component["artifact_sha256"])
    download_dir = target / "download"
    archive = _find_hash_locked_archive(download_dir, expected_hash)
    if archive is None:
        command = [
            sys.executable, str(RESOURCE_ENTRY), "resource", "materialize-url", str(component["artifact_url"]),
            "--target-dir", str(download_dir), "--name", str(component["artifact_name"]),
            "--max-bytes", str(component["max_bytes"]), "--sha256", expected_hash,
            "--source", f"github:{component['id']}", "--task", f"Install pinned {component['id']} for WSL developer toolchain",
            "--purpose", "developer_toolchain_bootstrap", "--validation-profile", "live",
            "--download-backend", "auto", "--timeout", "120", "--retries", "1",
            "--store-root", str(paths["store"]), "--receipt-log", str(paths["receipts"]),
            "--resource-log", str(paths["resource_log"]), "--json",
        ]
        result = _json_result(command, timeout=300)
        if not result["ok"]:
            return {**result, "component": component["id"], "target": str(target)}
        archive = _find_hash_locked_archive(download_dir, expected_hash)
    if archive is None:
        return {"ok": False, "reason": "hash_locked_archive_missing", "component": component["id"]}
    source_paths = [_archive_source_path(target, item) for item in component.get("executables", [])]
    if not all(path.is_file() for path in source_paths):
        extraction = _safe_extract_tar(archive, target)
        if not extraction.get("ok"):
            return {**extraction, "component": component["id"]}
    bridges = [_write_credential_bridge_wrapper(target, item) for item in component.get("executables", [])]
    bridge_failure = next((item for item in bridges if not item.get("ok")), None)
    if bridge_failure:
        return {**bridge_failure, "component": component["id"], "archive": str(archive)}
    expected_paths = [target / str(item["relative_path"]) for item in component.get("executables", [])]
    return {
        "ok": all(path.is_file() for path in expected_paths) and all(path.is_file() for path in source_paths),
        "component": component["id"],
        "archive": str(archive),
        "credential_bridges": bridges,
    }


def _install_binary_component(component: dict[str, Any], target: Path, paths: dict[str, Path]) -> dict[str, Any]:
    expected_hash = str(component["artifact_sha256"])
    download_dir = target / "download"
    artifact = _find_hash_locked_archive(download_dir, expected_hash)
    if artifact is None:
        command = [
            sys.executable, str(RESOURCE_ENTRY), "resource", "materialize-url", str(component["artifact_url"]),
            "--target-dir", str(download_dir), "--name", str(component["artifact_name"]),
            "--max-bytes", str(component["max_bytes"]), "--sha256", expected_hash,
            "--source", f"github:{component['id']}", "--task", f"Install pinned {component['id']} for WSL developer toolchain",
            "--purpose", "developer_toolchain_bootstrap", "--validation-profile", "live",
            "--download-backend", "auto", "--timeout", "120", "--retries", "1",
            "--store-root", str(paths["store"]), "--receipt-log", str(paths["receipts"]),
            "--resource-log", str(paths["resource_log"]), "--json",
        ]
        result = _json_result(command, timeout=300)
        if not result["ok"]:
            return {**result, "component": component["id"], "target": str(target)}
        artifact = _find_hash_locked_archive(download_dir, expected_hash)
    if artifact is None:
        return {"ok": False, "reason": "hash_locked_binary_missing", "component": component["id"]}
    executables = component.get("executables", [])
    if len(executables) != 1:
        return {"ok": False, "reason": "binary_provider_requires_one_executable", "component": component["id"]}
    destination = target / str(executables[0]["relative_path"])
    if not _inside(destination, target):
        return {"ok": False, "reason": "binary_target_path_escape", "component": component["id"]}
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.toolchain-{os.getpid()}")
    shutil.copyfile(artifact, temporary)
    temporary.chmod(0o755)
    os.replace(temporary, destination)
    return {
        "ok": destination.is_file() and sha256_file(destination) == expected_hash,
        "component": component["id"],
        "artifact": str(artifact),
        "executable": str(destination),
    }


def apply(*, confirm: str) -> dict[str, Any]:
    if confirm != INSTALL_CONFIRM:
        return {
            "schema": "developer_toolchain_owner.apply.v1",
            "ok": False,
            "status": "blocked",
            "reason": f"pass --confirm {INSTALL_CONFIRM}",
            "writes_files": False,
        }
    lock = load_lock()
    install_root = _path(str(lock["install_root"]))
    bin_dir = _path(str(lock["bin_dir"]))
    paths = _resource_paths(lock)
    operations: list[dict[str, Any]] = []
    for component in lock["components"]:
        if component.get("provider") == "codex_runtime":
            continue
        target = install_root / str(component["target_subdir"])
        expected = [target / str(item["relative_path"]) for item in component.get("executables", [])]
        if not all(path.is_file() for path in expected):
            if component.get("provider") == "python_package":
                installed = _install_python_component(component, target, paths)
            elif component.get("provider") == "github_release_tarball":
                installed = _install_archive_component(component, target, paths)
            elif component.get("provider") == "github_release_binary":
                installed = _install_binary_component(component, target, paths)
            else:
                installed = {"ok": False, "reason": "unsupported_provider", "component": component.get("id")}
            operations.append(installed)
            if not installed.get("ok"):
                return {
                    "schema": "developer_toolchain_owner.apply.v1",
                    "ok": False,
                    "status": "failed",
                    "operations": operations,
                    "writes_files": True,
                    "system_install_used": False,
                }
        for executable, target_path in zip(component.get("executables", []), expected):
            target_path.chmod(0o755)
            projected = _project_executable(target_path, bin_dir / str(executable["name"]), install_root)
            operations.append({"component": component["id"], "projection": projected})
            if not projected.get("ok"):
                return {
                    "schema": "developer_toolchain_owner.apply.v1",
                    "ok": False,
                    "status": "failed",
                    "operations": operations,
                    "writes_files": True,
                    "system_install_used": False,
                }
            if str(executable.get("name") or "") == "gh":
                appserver_projection = _project_appserver_path_shim(target_path)
                operations.append({"component": component["id"], "appserver_path_projection": appserver_projection})
                if not appserver_projection.get("ok"):
                    return {
                        "schema": "developer_toolchain_owner.apply.v1",
                        "ok": False,
                        "status": "failed",
                        "operations": operations,
                        "writes_files": True,
                        "system_install_used": False,
                    }
    final = snapshot(lock, install_root=install_root, bin_dir=bin_dir)
    receipt = BRIDGE_ROOT / "runtime" / "developer_toolchain" / "last-apply.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "schema": "developer_toolchain_owner.apply.v1",
        "ok": bool(final["ok"]),
        "status": "completed" if final["ok"] else "failed",
        "generated_at": now_iso(),
        "operations": operations,
        "validation": final,
        "writes_files": True,
        "system_install_used": False,
        "old_versions_deleted": False,
        "receipt": str(receipt),
    }
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def validate() -> dict[str, Any]:
    state = snapshot()
    return {
        "schema": "developer_toolchain_owner.validate.v1",
        "ok": bool(state["ok"]),
        "generated_at": now_iso(),
        "missing_required": state["missing_required"],
        "tool_count": len(state["tools"]),
        "snapshot": state,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Version-locked WSL developer toolchain owner")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("plan")
    sub.add_parser("validate")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.command == "snapshot":
        payload = snapshot()
    elif args.command == "plan":
        payload = plan()
    elif args.command == "validate":
        payload = validate()
    else:
        payload = apply(confirm=str(args.confirm or ""))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
