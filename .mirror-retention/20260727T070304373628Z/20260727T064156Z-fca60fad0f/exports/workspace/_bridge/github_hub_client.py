#!/usr/bin/env python3
"""GitHub REST client for Local MCP Hub.

Ownership: credential-source selection and GitHub REST API calls for Hub
`github.api`.
Non-goals: GitHub CLI command policy, native GitHub MCP routing, or secret
printing.
State behavior: reads environment variables and Secret Vault aliases; generated
GitHub App installation tokens are in-process only and are not persisted.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_ROOT = ROOT / "_bridge"
sys.path.insert(0, str(BRIDGE_ROOT))

from github_app_auth import create_installation_token as github_app_create_installation_token  # noqa: E402
from secret_vault import get_secret as secret_vault_get_secret  # noqa: E402

WRITE_ACK = "github-write-through-hub-uses-existing-permissions"
ENV_TOKEN_KEYS = ("GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")
APP_ALIASES = ["github_app.app_id", "github_app.installation_id", "github_app.private_key"]
WINDOWS_GH_PATHS = (
    Path(r"C:\Program Files\GitHub CLI\gh.exe"),
    Path("/mnt/c/Program Files/GitHub CLI/gh.exe"),
)


def github_cli_candidates() -> list[str]:
    """Return platform-valid gh executables without persisting PATH changes."""

    candidates = [
        os.environ.get("CODEX_GH_PATH", "").strip(),
        shutil.which("gh"),
        shutil.which("gh.exe"),
        *(str(path) for path in WINDOWS_GH_PATHS if path.is_file()),
    ]
    return list(dict.fromkeys(item for item in candidates if item))


def resolve_github_cli() -> str:
    candidates = github_cli_candidates()
    return candidates[0] if candidates else "gh"


def _gh_keyring_token() -> str:
    """Read the existing gh keyring token only for the in-process API call."""
    for executable in github_cli_candidates():
        try:
            result = subprocess.run(
                [executable, "auth", "token"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            token = (result.stdout or "").strip()
            if token:
                return token
    return ""


def github_auth_candidates() -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for key in ENV_TOKEN_KEYS:
        value = str(os.environ.get(key) or "").strip()
        if value:
            candidates.append((value, f"environment:{key}"))
    try:
        token, metadata = github_app_create_installation_token(timeout_seconds=30)
        if token.strip():
            expires_at = str(metadata.get("expires_at") or "").strip()
            source = f"github_app.installation_token{f':expires_at={expires_at}' if expires_at else ''}"
            candidates.append((token.strip(), source))
    except Exception:
        pass
    try:
        token = secret_vault_get_secret("github.token").strip()
        if token:
            candidates.append((token, "secret_vault:github.token"))
    except Exception:
        pass
    token = _gh_keyring_token()
    if token:
        candidates.append((token, "gh_keyring"))
    return candidates


def github_api(arguments: dict[str, Any]) -> dict[str, Any]:
    method = str(arguments.get("method") or "GET").strip().upper()
    path = str(arguments.get("path") or "").strip()
    query = arguments.get("query") if isinstance(arguments.get("query"), dict) else {}
    body = arguments.get("body") if isinstance(arguments.get("body"), dict) else {}
    timeout_seconds = int(arguments.get("timeout_seconds") or 45)
    write_ack = str(arguments.get("write_ack") or "").strip()
    if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
        return {"ok": False, "reason": "invalid_method", "method": method}
    if method != "GET" and write_ack != WRITE_ACK:
        return {"ok": False, "reason": "write_ack_required", "required": WRITE_ACK, "method": method, "path": path}
    if not path:
        return {"ok": False, "reason": "path_required"}
    api_path, query = normalize_api_path(path, query)
    token_candidates = github_auth_candidates()
    if not token_candidates:
        return {
            "ok": False,
            "reason": "github_token_missing",
            "accepted_env": list(ENV_TOKEN_KEYS),
            "accepted_secret_aliases": [*APP_ALIASES, "github.token"],
            "accepted_in_process_fallbacks": ["gh_keyring"],
        }
    return call_with_credential_chain(
        method=method,
        api_path=api_path,
        query=query,
        body=body,
        timeout_seconds=timeout_seconds,
        token_candidates=token_candidates,
    )


def normalize_api_path(path: str, query: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if path.startswith("https://") or path.startswith("http://"):
        parsed = urllib.parse.urlparse(path)
        if parsed.scheme != "https" or parsed.netloc != "api.github.com":
            raise ValueError(f"only_api_github_com_urls_allowed:{parsed.netloc}")
        merged_query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        merged_query.update({str(k): str(v) for k, v in query.items()})
        return parsed.path, merged_query
    return "/" + path.lstrip("/"), query


def call_with_credential_chain(
    *,
    method: str,
    api_path: str,
    query: dict[str, Any],
    body: dict[str, Any],
    timeout_seconds: int,
    token_candidates: list[tuple[str, str]],
) -> dict[str, Any]:
    query_string = urllib.parse.urlencode({str(k): str(v) for k, v in query.items() if v is not None})
    url = f"https://api.github.com{api_path}" + (f"?{query_string}" if query_string else "")
    data = None if method == "GET" else json.dumps(body, ensure_ascii=False).encode("utf-8")
    attempted_sources: list[str] = []
    last_credential_error: dict[str, Any] | None = None
    for token, token_source in token_candidates:
        attempted_sources.append(token_source)
        result = call_once(
            method=method,
            url=url,
            api_path=api_path,
            data=data,
            token=token,
            token_source=token_source,
            attempted_sources=attempted_sources,
            timeout_seconds=timeout_seconds,
        )
        if result.get("ok") or result.get("status") != 401:
            return result
        if len(attempted_sources) >= len(token_candidates):
            return result
        last_credential_error = result
    return last_credential_error or {"ok": False, "reason": "all_github_credentials_failed", "attempted_token_sources": attempted_sources}


def call_once(
    *,
    method: str,
    url: str,
    api_path: str,
    data: bytes | None,
    token: str,
    token_source: str,
    attempted_sources: list[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "local-mcp-hub",
    }
    if data is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=max(1, min(timeout_seconds, 120))) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
            response_headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": int(exc.code),
            "method": method,
            "path": api_path,
            "body": raw[:12000],
            "rate_limit_remaining": exc.headers.get("x-ratelimit-remaining", ""),
            "token_source": token_source or "unknown",
            "attempted_token_sources": list(attempted_sources),
        }
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}", "method": method, "path": api_path, "token_source": token_source or "unknown"}
    return {
        "ok": 200 <= status < 300,
        "status": status,
        "method": method,
        "path": api_path,
        "result": parse_body(raw),
        "rate_limit_remaining": response_headers.get("x-ratelimit-remaining", ""),
        "token_source": token_source or "unknown",
        "attempted_token_sources": list(attempted_sources),
    }


def parse_body(raw: str) -> Any:
    if raw.strip():
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw[:20000]
    return raw
