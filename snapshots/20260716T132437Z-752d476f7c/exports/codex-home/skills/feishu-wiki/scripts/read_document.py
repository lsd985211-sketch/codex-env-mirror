#!/usr/bin/env python3
"""Read Feishu docx raw content or block structure through Open API."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

import requests

BASE_URL = "https://open.feishu.cn/open-apis"
DEFAULT_TIMEOUT = 20.0


class FeishuError(RuntimeError):
    pass


def api_result(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise FeishuError(f"non_json_response:http_{response.status_code}") from exc
    if response.status_code >= 400:
        raise FeishuError(f"http_{response.status_code}:{payload.get('msg', 'request_failed')}")
    if payload.get("code", 0) != 0:
        raise FeishuError(f"api_{payload.get('code')}:{payload.get('msg', 'request_failed')}")
    return payload


def tenant_token(session: requests.Session, base_url: str, timeout: float) -> str:
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise FeishuError("missing_credentials:FEISHU_APP_ID_and_FEISHU_APP_SECRET_required")
    response = session.post(
        f"{base_url}/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=timeout,
    )
    token = api_result(response).get("tenant_access_token", "")
    if not token:
        raise FeishuError("tenant_access_token_missing")
    return str(token)


def document_id(value: str) -> str:
    match = re.search(r"/(?:docx|docs)/([A-Za-z0-9_-]+)", value)
    return match.group(1) if match else value.strip()


def get_raw_content(
    session: requests.Session, base_url: str, token: str, doc_id: str, timeout: float
) -> dict[str, Any]:
    response = session.get(
        f"{base_url}/docx/v1/documents/{doc_id}/raw_content",
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    return api_result(response).get("data", {})


def get_blocks(
    session: requests.Session, base_url: str, token: str, doc_id: str, timeout: float
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {"page_size": 500, "document_revision_id": -1}
        if page_token:
            params["page_token"] = page_token
        response = session.get(
            f"{base_url}/docx/v1/documents/{doc_id}/blocks",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=timeout,
        )
        data = api_result(response).get("data", {})
        items.extend(data.get("items", []))
        if not data.get("has_more"):
            return items
        page_token = str(data.get("page_token") or "")
        if not page_token:
            raise FeishuError("pagination_missing_page_token")


def main() -> int:
    parser = argparse.ArgumentParser(description="Read a Feishu docx document")
    parser.add_argument("document", help="Feishu document URL or document token")
    parser.add_argument("--mode", choices=("raw", "blocks", "both"), default="raw")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()

    doc_id = document_id(args.document)
    if not doc_id:
        print(json.dumps({"ok": False, "error": "document_id_missing"}))
        return 2
    try:
        with requests.Session() as session:
            token = tenant_token(session, args.base_url.rstrip("/"), args.timeout)
            result: dict[str, Any] = {"ok": True, "document_id": doc_id, "mode": args.mode}
            if args.mode in {"raw", "both"}:
                result["raw"] = get_raw_content(session, args.base_url.rstrip("/"), token, doc_id, args.timeout)
            if args.mode in {"blocks", "both"}:
                result["blocks"] = get_blocks(session, args.base_url.rstrip("/"), token, doc_id, args.timeout)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
    except (FeishuError, requests.RequestException) as exc:
        print(json.dumps({"ok": False, "document_id": doc_id, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
