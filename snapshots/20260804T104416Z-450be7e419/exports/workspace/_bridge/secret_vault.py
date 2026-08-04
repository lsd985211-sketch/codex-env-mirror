#!/usr/bin/env python3
"""Secret vault backed by Windows Credential Manager.

Ownership: secret aliases, metadata, and controlled retrieval for local tools.
Non-goals: printing secret values, storing raw secrets in memory/profile files,
or bypassing each consumer's existing permission boundary.
State behavior: metadata is JSON without secret values; values are stored in the
current Windows user's Credential Manager through win32cred.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import win32cred
except Exception:  # pragma: no cover - platform/backend guard
    win32cred = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "_bridge" / "runtime" / "secret_vault"
INDEX_PATH = RUNTIME_DIR / "metadata.json"
SERVICE_PREFIX = "codex-secret-vault"
SCHEMA = "secret_vault.metadata.v1"
SECRET_SHAPE_RE = re.compile(
    r"gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|(token|secret|password|passwd|api[_-]?key)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


class SecretVaultError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_backend() -> None:
    if win32cred is None:
        raise SecretVaultError("win32cred backend unavailable")


def _load_index() -> dict[str, Any]:
    if not INDEX_PATH.exists():
        return {"schema": SCHEMA, "updated_at": now_iso(), "entries": {}}
    payload = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SecretVaultError("metadata root is not object")
    payload.setdefault("schema", SCHEMA)
    payload.setdefault("entries", {})
    if not isinstance(payload.get("entries"), dict):
        raise SecretVaultError("metadata entries is not object")
    return payload


def _write_index(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if SECRET_SHAPE_RE.search(text):
        raise SecretVaultError("metadata appears to contain secret-like content")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(text + "\n", encoding="utf-8")


def credential_target(alias: str) -> str:
    clean = alias.strip()
    if not clean or not re.fullmatch(r"[A-Za-z0-9_.:-]{3,128}", clean):
        raise SecretVaultError("invalid alias")
    return f"{SERVICE_PREFIX}:{clean}"


def _fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


def _write_secret(alias: str, secret: str) -> None:
    _ensure_backend()
    if not secret:
        raise SecretVaultError("empty secret")
    target = credential_target(alias)
    win32cred.CredWrite(
        {
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": target,
            "UserName": getpass.getuser(),
            "CredentialBlob": secret,
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
        },
        0,
    )


def _read_secret(alias: str) -> str:
    _ensure_backend()
    target = credential_target(alias)
    try:
        cred = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC, 0)
    except Exception as exc:
        raise SecretVaultError("secret not found") from exc
    blob = cred.get("CredentialBlob", "")
    if isinstance(blob, bytes):
        try:
            return blob.decode("utf-16-le")
        except UnicodeDecodeError:
            return blob.decode("utf-8", errors="replace")
    return str(blob or "")


def store(alias: str, category: str, purpose: str, secret: str, *, owner: str = "", rotation_note: str = "") -> dict[str, Any]:
    _write_secret(alias, secret)
    index = _load_index()
    entries = index["entries"]
    entries[alias] = {
        "alias": alias,
        "category": category,
        "purpose": purpose,
        "owner": owner,
        "backend": "windows_credential_manager",
        "target_name": credential_target(alias),
        "created_or_updated_at": now_iso(),
        "secret_present": True,
        "secret_length": len(secret),
        "secret_sha256_16": _fingerprint(secret),
        "rotation_note": rotation_note,
        "read_policy": "no_print; consumer_handoff_only",
    }
    index["updated_at"] = now_iso()
    _write_index(index)
    return {"ok": True, "alias": alias, "category": category, "backend": "windows_credential_manager", "secret_present": True, "secret_sha256_16": _fingerprint(secret)}


def get_secret(alias: str) -> str:
    return _read_secret(alias)


def snapshot() -> dict[str, Any]:
    index = _load_index()
    entries = []
    for alias, item in sorted(index.get("entries", {}).items()):
        if not isinstance(item, dict):
            continue
        redacted = {k: v for k, v in item.items() if k not in {"secret", "value", "token", "password"}}
        redacted["backend_readable"] = _is_readable(alias)
        entries.append(redacted)
    return {
        "schema": "secret_vault.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "backend": "windows_credential_manager",
        "metadata_path": str(INDEX_PATH),
        "entry_count": len(entries),
        "entries": entries,
        "secret_values_returned": False,
    }


def _is_readable(alias: str) -> bool:
    try:
        return bool(_read_secret(alias))
    except Exception:
        return False


def doctor() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    backend_ok = win32cred is not None
    if not backend_ok:
        issues.append({"severity": "risk", "code": "win32cred_backend_unavailable"})
    try:
        index = _load_index()
    except Exception as exc:
        index = {"entries": {}}
        issues.append({"severity": "risk", "code": "metadata_unreadable", "detail": str(exc)})
    for alias, item in sorted((index.get("entries") or {}).items()):
        if not isinstance(item, dict):
            issues.append({"severity": "risk", "code": "metadata_entry_invalid", "alias": alias})
            continue
        if any(key in item for key in ("secret", "value", "token", "password")):
            issues.append({"severity": "risk", "code": "metadata_contains_forbidden_secret_field", "alias": alias})
        if backend_ok and not _is_readable(alias):
            issues.append({"severity": "risk", "code": "secret_backend_value_missing", "alias": alias})
    return {
        "schema": "secret_vault.doctor.v1",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "backend_ok": backend_ok,
        "issues": issues,
    }


def validate() -> dict[str, Any]:
    alias = "__selftest.local"
    secret = f"selftest-{now_iso()}"
    try:
        store(alias, "selftest", "non-secret backend roundtrip validation", secret, rotation_note="temporary")
        read_back = _read_secret(alias)
        ok = read_back == secret
    except Exception as exc:
        return {"schema": "secret_vault.validate.v1", "ok": False, "reason": f"{type(exc).__name__}: {exc}", "secret_values_returned": False}
    finally:
        try:
            _ensure_backend()
            win32cred.CredDelete(credential_target(alias), win32cred.CRED_TYPE_GENERIC, 0)
        except Exception:
            pass
        try:
            index = _load_index()
            index.get("entries", {}).pop(alias, None)
            index["updated_at"] = now_iso()
            _write_index(index)
        except Exception:
            pass
    return {"schema": "secret_vault.validate.v1", "ok": ok, "backend": "windows_credential_manager", "roundtrip": ok, "secret_values_returned": False}


def read_stdin_secret() -> str:
    value = sys.stdin.read()
    if not value:
        return ""
    return value.rstrip("\r\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Secret Vault")
    sub = parser.add_subparsers(dest="command", required=True)
    store_p = sub.add_parser("store")
    store_p.add_argument("--alias", required=True)
    store_p.add_argument("--category", required=True)
    store_p.add_argument("--purpose", required=True)
    store_p.add_argument("--owner", default="")
    store_p.add_argument("--rotation-note", default="")
    store_p.add_argument("--stdin", action="store_true", help="Read secret value from stdin; never pass secrets as arguments.")
    for name in ("snapshot", "doctor", "validate"):
        sub.add_parser(name)
    get_p = sub.add_parser("get")
    get_p.add_argument("--alias", required=True)
    get_p.add_argument("--allow-print", action="store_true", help="Explicit break-glass mode. Avoid in normal Codex work.")
    args = parser.parse_args()
    try:
        if args.command == "store":
            if not args.stdin:
                payload = {"ok": False, "reason": "stdin_required_for_secret_value"}
            else:
                payload = store(str(args.alias), str(args.category), str(args.purpose), read_stdin_secret(), owner=str(args.owner), rotation_note=str(args.rotation_note))
        elif args.command == "snapshot":
            payload = snapshot()
        elif args.command == "doctor":
            payload = doctor()
        elif args.command == "validate":
            payload = validate()
        elif args.command == "get":
            if not args.allow_print:
                payload = {"ok": False, "reason": "secret_printing_blocked", "alias": str(args.alias)}
            else:
                print(get_secret(str(args.alias)), end="")
                return 0
        else:
            payload = {"ok": False, "reason": "unknown_command"}
    except Exception as exc:
        payload = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
