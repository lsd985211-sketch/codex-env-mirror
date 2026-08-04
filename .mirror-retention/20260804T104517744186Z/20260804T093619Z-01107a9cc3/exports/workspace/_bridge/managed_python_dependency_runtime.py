#!/usr/bin/env python3
"""Resolve and verify ABI-scoped managed Python dependency trees.

Ownership: runtime identity, ABI/platform target selection, and fresh-process
import acceptance for owner-managed ``pip --target`` trees.
Non-goals: package discovery, network access, installation approval, global
site-packages, interpreter installation, or dependency version selection.
State behavior: read-only; callers own package installation and atomic writes.
Caller context: the resource Python package installer and Windows-projected
resource-search worker share this authority so they cannot disagree on paths.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import json
import os
import platform
import re
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "managed_python_dependency_runtime.v1"
DEPENDENCY_LOCK_PATH = Path(__file__).resolve().parent / "policies" / "managed_python_dependencies.lock.json"
IMPORT_CONTRACTS: dict[str, tuple[str, ...]] = {
    "beautifulsoup4": ("bs4",),
    "ddgs": ("ddgs", "lxml.etree"),
    "headroom-ai": ("headroom", "headroom.ccr.mcp_server", "mcp.server", "tree_sitter_language_pack"),
}


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", str(value or "").strip()).lower()


def runtime_identity() -> dict[str, str]:
    version = sys.version_info
    machine = platform.machine().lower().replace("-", "_") or "unknown"
    if sys.platform == "win32":
        platform_tag = "win_amd64" if machine in {"amd64", "x86_64"} else f"win_{machine}"
    elif sys.platform.startswith("linux"):
        platform_tag = f"linux_{machine}"
    else:
        platform_tag = f"{sys.platform}_{machine}"
    return {
        "implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "abi_tag": f"cp{version.major}{version.minor}",
        "platform": sys.platform,
        "platform_tag": platform_tag,
        "machine": platform.machine(),
        "ext_suffix": str(sysconfig.get_config_var("EXT_SUFFIX") or ""),
        "executable": sys.executable,
    }


def runtime_key(identity: dict[str, Any] | None = None) -> str:
    value = identity or runtime_identity()
    abi_tag = str(value.get("abi_tag") or "unknown_abi").strip().lower()
    platform_tag = str(value.get("platform_tag") or "unknown_platform").strip().lower()
    return f"{abi_tag}-{platform_tag}"


def required_imports(package_name: str) -> tuple[str, ...]:
    return IMPORT_CONTRACTS.get(canonical_name(package_name), ())


def locked_dependency(package_name: str) -> dict[str, Any]:
    canonical = canonical_name(package_name)
    try:
        payload = json.loads(DEPENDENCY_LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    dependencies = payload.get("dependencies") if isinstance(payload.get("dependencies"), dict) else {}
    entry = dependencies.get(canonical)
    return dict(entry) if isinstance(entry, dict) else {}


def dependency_lock_signature(package_name: str) -> str:
    """Return a stable signature for one managed dependency contract."""
    entry = locked_dependency(package_name)
    if not entry:
        return ""
    canonical = json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def managed_dependency_plan(package_name: str) -> dict[str, Any]:
    """Project the owner lock into a read-only install plan."""
    canonical = canonical_name(package_name)
    entry = locked_dependency(canonical)
    requirements = entry.get("requirements") if isinstance(entry.get("requirements"), list) else []
    complete = bool(
        entry.get("version")
        and requirements
        and len(locked_requirement_lines(canonical)) == len(requirements)
    )
    target = scoped_dependency_target(Path(__file__).resolve().parent / "runtime_dependencies", canonical)
    return {
        "schema": f"{SCHEMA}.install_plan",
        "ok": complete,
        "package": canonical,
        "locked_spec": f"{canonical}=={entry.get('version')}" if entry.get("version") else "",
        "version": str(entry.get("version") or ""),
        "requirements": [
            {
                "name": canonical_name(str(row.get("name") or "")),
                "version": str(row.get("version") or ""),
                "url": str(row.get("url") or ""),
                "sha256": str(row.get("sha256") or ""),
            }
            for row in requirements
            if isinstance(row, dict)
        ],
        "required_imports": list(required_imports(canonical)),
        "runtime": runtime_identity(),
        "runtime_key": runtime_key(),
        "target_dir": str(target),
        "lock_path": str(DEPENDENCY_LOCK_PATH),
        "lock_signature": dependency_lock_signature(canonical),
        "hash_checking_required": complete,
        "risk": "local_filesystem_write_and_code_import",
    }


def locked_requirement_lines(package_name: str) -> tuple[str, ...]:
    entry = locked_dependency(package_name)
    rows = entry.get("requirements") if isinstance(entry.get("requirements"), list) else []
    result: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = canonical_name(str(row.get("name") or ""))
        version = str(row.get("version") or "").strip()
        url = str(row.get("url") or "").strip()
        digest = str(row.get("sha256") or "").strip().lower()
        if name and version and url.startswith("https://files.pythonhosted.org/") and re.fullmatch(r"[0-9a-f]{64}", digest):
            result.append(f"{name} @ {url}#sha256={digest}")
    return tuple(result)


def legacy_dependency_target(dependency_root: Path, package_name: str) -> Path:
    return Path(dependency_root) / canonical_name(package_name)


def scoped_dependency_target(
    dependency_root: Path,
    package_name: str,
    identity: dict[str, Any] | None = None,
) -> Path:
    return legacy_dependency_target(dependency_root, package_name) / runtime_key(identity)


def _has_payload(path: Path, package_name: str) -> bool:
    canonical = canonical_name(package_name)
    package_dir = path / canonical.replace("-", "_")
    if package_dir.is_dir():
        return True
    if not path.is_dir():
        return False
    return any(any(path.glob(f"{stem}-*.dist-info")) for stem in {canonical, canonical.replace("-", "_")})


def probe_imports(
    target: Path,
    imports: Iterable[str],
    *,
    python_executable: Path | str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    modules = tuple(str(item).strip() for item in imports if str(item).strip())
    if not modules:
        return {"schema": f"{SCHEMA}.import_probe", "ok": True, "target": str(target), "imports": []}
    if not target.is_dir():
        return {
            "schema": f"{SCHEMA}.import_probe",
            "ok": False,
            "reason": "dependency_target_missing",
            "target": str(target),
            "imports": list(modules),
        }
    executable = Path(python_executable or sys.executable)
    script = """
import importlib, json, os, platform, sys, sysconfig
from pathlib import Path
target = Path(sys.argv[1]).resolve()
names = json.loads(sys.argv[2])
sys.path.insert(0, str(target))
rows = []
ok = True
for name in names:
    try:
        module = importlib.import_module(name)
        origin = str(getattr(module, "__file__", "") or "")
        inside = bool(origin) and os.path.commonpath([str(target), str(Path(origin).resolve())]) == str(target)
        rows.append({"name": name, "ok": inside, "origin": origin, "origin_within_target": inside})
        ok = ok and inside
    except Exception as exc:
        rows.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        ok = False
version = sys.version_info
machine = platform.machine().lower().replace("-", "_") or "unknown"
platform_tag = ("win_amd64" if machine in {"amd64", "x86_64"} else f"win_{machine}") if sys.platform == "win32" else f"linux_{machine}" if sys.platform.startswith("linux") else f"{sys.platform}_{machine}"
print(json.dumps({"ok": ok, "runtime": {"implementation": platform.python_implementation(), "python_version": platform.python_version(), "abi_tag": f"cp{version.major}{version.minor}", "platform": sys.platform, "platform_tag": platform_tag, "ext_suffix": str(sysconfig.get_config_var("EXT_SUFFIX") or ""), "executable": sys.executable}, "imports": rows}))
""".strip()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
    try:
        completed = subprocess.run(
            [str(executable), "-I", "-c", script, str(target), json.dumps(list(modules))],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, min(int(timeout), 60)),
            env=env,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "schema": f"{SCHEMA}.import_probe",
            "ok": False,
            "reason": "import_probe_execution_failed",
            "error": f"{type(exc).__name__}: {exc}",
            "target": str(target),
            "imports": list(modules),
            "python": str(executable),
        }
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        payload = {"ok": False, "reason": "import_probe_invalid_json", "error": str(exc)}
    if not isinstance(payload, dict):
        payload = {"ok": False, "reason": "import_probe_non_object"}
    return {
        "schema": f"{SCHEMA}.import_probe",
        **payload,
        "ok": bool(completed.returncode == 0 and payload.get("ok")),
        "target": str(target),
        "python": str(executable),
        "returncode": completed.returncode,
        "stderr": str(completed.stderr or "")[-2000:],
    }


def select_dependency_target(
    dependency_root: Path,
    package_name: str,
    *,
    identity: dict[str, Any] | None = None,
    python_executable: Path | str | None = None,
) -> dict[str, Any]:
    current_identity = dict(identity or runtime_identity())
    scoped = scoped_dependency_target(dependency_root, package_name, current_identity)
    imports = required_imports(package_name)
    if _has_payload(scoped, package_name):
        smoke = probe_imports(scoped, imports, python_executable=python_executable)
        return {
            "schema": f"{SCHEMA}.selection",
            "ok": bool(smoke.get("ok")),
            "reason": "runtime_scoped_dependency_ready" if smoke.get("ok") else "runtime_scoped_dependency_incompatible",
            "selected_kind": "runtime_scoped",
            "path": str(scoped),
            "runtime": current_identity,
            "runtime_key": runtime_key(current_identity),
            "import_probe": smoke,
        }
    legacy = legacy_dependency_target(dependency_root, package_name)
    legacy_probe: dict[str, Any] = {}
    if _has_payload(legacy, package_name):
        legacy_probe = probe_imports(legacy, imports, python_executable=python_executable)
        if legacy_probe.get("ok"):
            return {
                "schema": f"{SCHEMA}.selection",
                "ok": True,
                "reason": "compatible_legacy_dependency_selected",
                "selected_kind": "legacy_compatible",
                "path": str(legacy),
                "runtime": current_identity,
                "runtime_key": runtime_key(current_identity),
                "import_probe": legacy_probe,
                "next_action": "reinstall_through_resource_owner_to_materialize_runtime_scoped_target",
            }
    return {
        "schema": f"{SCHEMA}.selection",
        "ok": False,
        "reason": "runtime_scoped_dependency_missing",
        "selected_kind": "runtime_scoped",
        "path": str(scoped),
        "runtime": current_identity,
        "runtime_key": runtime_key(current_identity),
        "legacy_path": str(legacy),
        "legacy_probe": legacy_probe,
        "required_imports": list(imports),
        "next_action": "install_package_through_resource_owner_for_the_reported_runtime_key",
    }


def validate() -> dict[str, Any]:
    identity = runtime_identity()
    key = runtime_key(identity)
    suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
    locked = locked_dependency("beautifulsoup4")
    locked_lines = locked_requirement_lines("beautifulsoup4")
    return {
        "schema": f"{SCHEMA}.validate",
        "ok": bool(re.fullmatch(r"cp\d+-.+", key) and suffixes and locked.get("version") == "4.15.0" and len(locked_lines) == 3),
        "runtime": identity,
        "runtime_key": key,
        "extension_suffixes": list(suffixes),
        "import_contracts": {name: list(items) for name, items in IMPORT_CONTRACTS.items()},
        "dependency_lock_path": str(DEPENDENCY_LOCK_PATH),
        "locked_dependencies": ["beautifulsoup4"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate managed Python dependency runtime identity")
    parser.add_argument("command", choices=("validate", "select"))
    parser.add_argument("--dependency-root", type=Path)
    parser.add_argument("--package", default="ddgs")
    parser.add_argument("--python-executable", type=Path)
    args = parser.parse_args(argv)
    if args.command == "validate":
        payload = validate()
    elif args.dependency_root is None:
        payload = {
            "schema": f"{SCHEMA}.selection",
            "ok": False,
            "reason": "dependency_root_required",
        }
    else:
        payload = select_dependency_target(
            args.dependency_root,
            args.package,
            python_executable=args.python_executable,
        )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
