#!/usr/bin/env python3
"""GitHub App authentication through the local Secret Vault.

Ownership: creating short-lived GitHub App JWTs and installation tokens for
Hub/GitHub consumers.
Non-goals: printing private keys, persisting installation tokens, or expanding
GitHub permissions beyond the installed App's own permission set.
State behavior: App ID, installation ID, and private key are read from Secret
Vault aliases; generated tokens stay in-process and expire quickly.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = ROOT / "_bridge"
sys.path.insert(0, str(BRIDGE_ROOT))

from secret_vault import get_secret, snapshot as secret_vault_snapshot  # noqa: E402
from shared.json_cli import now_iso  # noqa: E402

APP_ID_ALIAS = "github_app.app_id"
INSTALLATION_ID_ALIAS = "github_app.installation_id"
PRIVATE_KEY_ALIAS = "github_app.private_key"
REQUIRED_ALIASES = [APP_ID_ALIAS, INSTALLATION_ID_ALIAS, PRIVATE_KEY_ALIAS]
API_VERSION = "2022-11-28"


class GitHubAppAuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubAppCredentials:
    app_id: str
    installation_id: str
    private_key_pem: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _json_b64url(payload: dict[str, Any]) -> str:
    return _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _secret(alias: str) -> str:
    value = get_secret(alias).strip()
    if not value:
        raise GitHubAppAuthError(f"missing_secret:{alias}")
    return value


def configured_aliases() -> dict[str, bool]:
    payload = secret_vault_snapshot()
    entries = payload.get("entries") if isinstance(payload, dict) else []
    present = {str(item.get("alias")): bool(item.get("backend_readable")) for item in entries if isinstance(item, dict)}
    return {alias: bool(present.get(alias)) for alias in REQUIRED_ALIASES}


def load_credentials() -> GitHubAppCredentials:
    return GitHubAppCredentials(
        app_id=_secret(APP_ID_ALIAS),
        installation_id=_secret(INSTALLATION_ID_ALIAS),
        private_key_pem=_secret(PRIVATE_KEY_ALIAS),
    )


def create_app_jwt(credentials: GitHubAppCredentials | None = None) -> str:
    creds = credentials or load_credentials()
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {
        "iat": now - 60,
        "exp": now + 9 * 60,
        "iss": creds.app_id,
    }
    signing_input = f"{_json_b64url(header)}.{_json_b64url(payload)}".encode("ascii")
    try:
        private_key = serialization.load_pem_private_key(creds.private_key_pem.encode("utf-8"), password=None)
    except Exception as exc:
        raise GitHubAppAuthError(f"private_key_unreadable:{type(exc).__name__}") from exc
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode('ascii')}.{_b64url(signature)}"


def _github_request(method: str, path: str, token: str, *, body: dict[str, Any] | None = None, timeout_seconds: int = 30) -> dict[str, Any]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "local-codex-github-app-auth",
            **({"Content-Type": "application/json; charset=utf-8"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1, min(timeout_seconds, 120))) as response:
            raw = response.read().decode("utf-8", errors="replace")
            headers = dict(response.headers.items())
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": int(exc.code),
            "body": raw[:4000],
            "rate_limit_remaining": exc.headers.get("x-ratelimit-remaining", ""),
        }
    parsed: Any = raw
    if raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw[:4000]
    return {
        "ok": 200 <= status < 300,
        "status": status,
        "result": parsed,
        "rate_limit_remaining": headers.get("x-ratelimit-remaining", ""),
    }


def create_installation_token(*, timeout_seconds: int = 30) -> tuple[str, dict[str, Any]]:
    creds = load_credentials()
    app_jwt = create_app_jwt(creds)
    payload = _github_request(
        "POST",
        f"/app/installations/{creds.installation_id}/access_tokens",
        app_jwt,
        body={},
        timeout_seconds=timeout_seconds,
    )
    if not payload.get("ok"):
        raise GitHubAppAuthError(f"installation_token_exchange_failed:{payload.get('status') or payload.get('reason')}")
    result = payload.get("result")
    if not isinstance(result, dict) or not str(result.get("token") or "").strip():
        raise GitHubAppAuthError("installation_token_missing_in_response")
    token = str(result["token"]).strip()
    metadata = {
        "expires_at": result.get("expires_at", ""),
        "repository_selection": result.get("repository_selection", ""),
        "permissions": result.get("permissions", {}),
        "rate_limit_remaining": payload.get("rate_limit_remaining", ""),
    }
    return token, metadata


def snapshot() -> dict[str, Any]:
    aliases = configured_aliases()
    return {
        "schema": "github_app_auth.snapshot.v1",
        "ok": True,
        "generated_at": now_iso(),
        "configured": all(aliases.values()),
        "aliases": aliases,
        "required_aliases": REQUIRED_ALIASES,
        "token_values_returned": False,
    }


def doctor() -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    aliases = configured_aliases()
    for alias, is_present in aliases.items():
        if not is_present:
            issues.append({"severity": "risk", "code": "secret_alias_missing", "alias": alias})
    if all(aliases.values()):
        try:
            create_app_jwt()
        except Exception as exc:
            issues.append({"severity": "risk", "code": "jwt_generation_failed", "detail": str(exc)})
    return {
        "schema": "github_app_auth.doctor.v1",
        "ok": not any(item.get("severity") == "risk" for item in issues),
        "generated_at": now_iso(),
        "configured": all(aliases.values()),
        "issues": issues,
        "token_values_returned": False,
    }


def validate(*, online: bool = False) -> dict[str, Any]:
    doc = doctor()
    if not doc.get("ok"):
        return {"schema": "github_app_auth.validate.v1", "ok": False, "reason": "doctor_failed", "doctor": doc, "token_values_returned": False}
    if not online:
        return {"schema": "github_app_auth.validate.v1", "ok": True, "mode": "offline", "jwt_generation": True, "token_values_returned": False}
    try:
        token, metadata = create_installation_token()
    except Exception as exc:
        return {"schema": "github_app_auth.validate.v1", "ok": False, "mode": "online", "reason": str(exc), "token_values_returned": False}
    return {
        "schema": "github_app_auth.validate.v1",
        "ok": bool(token),
        "mode": "online",
        "installation_token": "created_redacted",
        "metadata": metadata,
        "token_values_returned": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="GitHub App auth via Secret Vault")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "doctor"):
        sub.add_parser(name)
    validate_p = sub.add_parser("validate")
    validate_p.add_argument("--online", action="store_true", help="Exchange for an installation token without printing it.")
    token_p = sub.add_parser("installation-token")
    token_p.add_argument("--allow-print", action="store_true", help="Break-glass only. Normal consumers must use module handoff.")
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            payload = snapshot()
        elif args.command == "doctor":
            payload = doctor()
        elif args.command == "validate":
            payload = validate(online=bool(args.online))
        elif args.command == "installation-token":
            if not args.allow_print:
                payload = {"ok": False, "reason": "token_printing_blocked"}
            else:
                token, _ = create_installation_token()
                print(token, end="")
                return 0
        else:
            payload = {"ok": False, "reason": "unknown_command"}
    except Exception as exc:
        payload = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
