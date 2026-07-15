#!/usr/bin/env python3
"""High-level Codex-facing resource acquisition command helpers.

Ownership: ergonomic top-level resource commands that submit mature broker
requests and return consumable receipts for Codex.
Non-goals: low-level download implementation, owner-tool execution, background
process lifecycle, global proxy mutation, package installation approval, or
remote writes.
State behavior: creates governed resource request manifests/receipts through
`resource_broker`; no direct unmanaged writes.
Caller context: `resource_cli.py get` facade.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from codex_resource_delegation import build_delegation
from intent_routing import matched_terms
from resource_library_paths import default_artifact_dir
from resource_broker import handle_request, mark_request_consumed, request_from_payload
from resource_progress_view import request_progress_from_receipt


ACADEMIC_PAPER_TERMS = (
    "arxiv",
    "doi",
    "学术",
    "开放获取",
    "期刊",
    "会议",
    "论文",
)
ACADEMIC_PAPER_WORD_TERMS = ("academic", "conference", "journal", "paper", "proceedings", "scholar")
ACADEMIC_PAPER_PHRASE_TERMS = ("open access",)


def has_academic_paper_signal(text: str) -> bool:
    return bool(matched_terms(text, ACADEMIC_PAPER_TERMS + ACADEMIC_PAPER_PHRASE_TERMS + ACADEMIC_PAPER_WORD_TERMS))


def infer_get_intent(args: argparse.Namespace) -> str:
    if args.intent and args.intent != "unknown":
        return str(args.intent)
    if args.path:
        return "explicit_local_file"
    if args.download or args.need_materialization:
        return "explicit_user_url" if args.url or str(args.target or "").startswith(("http://", "https://")) else "external_dependency"
    text = f"{args.task} {args.target} {args.url}".lower()
    if has_academic_paper_signal(text):
        return "external_dependency"
    if matched_terms(text, ("github", "repo", "仓库", "项目")):
        return "external_dependency"
    if matched_terms(text, ("doc", "docs", "文档", "资料")):
        return "documentation_lookup"
    if args.url:
        return "inline_url_candidate"
    return "external_dependency"


def read_text_excerpt(paths: list[dict[str, str]], *, max_chars: int) -> dict[str, Any]:
    for item in paths:
        if item.get("kind") not in {"owner_result", "preview", "manifest"}:
            continue
        path = Path(str(item.get("path") or ""))
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return {"ok": False, "path": str(path), "reason": f"{type(exc).__name__}: {exc}"}
        return {
            "ok": True,
            "kind": item.get("kind", ""),
            "path": str(path),
            "chars": len(text),
            "excerpt": text[: max(0, max_chars)],
            "truncated": len(text) > max_chars,
        }
    return {"ok": False, "reason": "no_readable_text_result"}


def build_get_payload(args: argparse.Namespace) -> dict[str, Any]:
    need_materialization = bool(args.need_materialization or args.download)
    allow_filesystem_write = bool(args.allow_filesystem_write or args.download)
    validation_profile = args.validation_profile or ("quick" if args.fast else "")
    target_dir = args.target_dir or ""
    if not target_dir and need_materialization and allow_filesystem_write:
        target_dir = str(
            default_artifact_dir(
                name=args.name or args.target or "",
                url=args.url or "",
                path=args.path or "",
                task=args.task or args.target or "",
            ).expanduser().resolve()
        )
    return build_delegation(
        task=args.task or args.target or args.url or args.path,
        target=args.target or "",
        url=args.url or "",
        path=args.path or "",
        name=args.name or "",
        intent=infer_get_intent(args),
        need_materialization=need_materialization,
        allow_network=bool(args.allow_network),
        allow_filesystem_write=allow_filesystem_write,
        max_bytes=args.max_bytes,
        expected_sha256=args.sha256 or "",
        timeout_seconds=args.timeout,
        retry_budget=args.retries,
        target_dir=target_dir,
        auto_owner=bool(args.auto_owner),
        owner_execution_mode=args.owner_execution_mode,
        purpose=args.purpose or args.task or "",
        validation_profile=validation_profile,
        runtime=args.runtime or "generic",
        download_backend=args.download_backend or "",
        resume_download=bool(args.resume_download),
        package_ecosystem=args.package_ecosystem or "",
        package_action=args.package_action or "",
        windows_package_manager=args.windows_package_manager or "",
        package_id=args.package_id or "",
        winget_id=args.winget_id or "",
        verify_binary=args.verify_binary or "",
        install_approved=bool(args.install_approved),
        accept_winget_agreements=bool(args.accept_winget_agreements),
    )


def get_result_payload(args: argparse.Namespace) -> dict[str, Any]:
    delegation = build_get_payload(args)
    request = request_from_payload(delegation.get("request", {}))
    receipt = handle_request(
        request,
        event_log=Path(args.event_log).expanduser().resolve(),
        receipt_log=Path(args.receipt_log).expanduser().resolve(),
        resource_log=None if args.no_resource_log else Path(args.resource_log).expanduser().resolve(),
        store_root=Path(args.store_root).expanduser().resolve(),
    )
    receipt_payload = receipt.__dict__
    progress = request_progress_from_receipt(receipt_payload)
    required_paths = progress.get("required_consume_paths") or []
    text_result = read_text_excerpt(required_paths, max_chars=int(args.content_chars)) if args.read_result else {}
    if text_result.get("ok"):
        updated = mark_request_consumed(
            request_id=receipt.request_id,
            consumed_path=str(text_result.get("path") or ""),
            consumer="codex",
            receipt_log=Path(args.receipt_log).expanduser().resolve(),
        )
        if updated.get("consumption"):
            receipt_payload = updated
            progress = request_progress_from_receipt(receipt_payload)
            text_result["consumption_recorded"] = True
        else:
            text_result["consumption_recorded"] = False
            text_result["consumption_error"] = updated
    return {
        "schema": "resource_get.result.v1",
        "ok": bool(receipt.ok or receipt.status == "handoff_required"),
        "request_id": receipt.request_id,
        "status": receipt.status,
        "result_kind": receipt.result_kind,
        "next_action": progress.get("next_action", receipt.next_action),
        "resource_need_satisfied": receipt.status == "completed",
        "consume_required": bool(progress.get("consume_required")),
        "required_consume_paths": required_paths,
        "consume_contract": progress.get("consume_contract") or {},
        "progress": progress.get("progress") or {},
        "status_summary": progress.get("status_summary") or {},
        "exception": progress.get("exception") or {},
        "receipt": receipt_payload,
        "text_result": text_result,
        "commands": {
            "progress": f"python _bridge\\resource_cli.py job progress --request-id {receipt.request_id} --json",
            "receipt": f"python _bridge\\resource_cli.py job receipt --request-id {receipt.request_id} --json",
            "status": f"python _bridge\\resource_cli.py job status --request-id {receipt.request_id} --json",
            "consume": f"python _bridge\\resource_cli.py job consume --request-id {receipt.request_id} --consumed-path <path> --json",
        },
    }


def command_get(args: argparse.Namespace) -> int:
    payload = get_result_payload(args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"request_id={payload.get('request_id', '')}")
        print(f"status={payload.get('status', '')} result_kind={payload.get('result_kind', '')}")
        print(f"next_action={payload.get('next_action', '')}")
        text_result = payload.get("text_result") if isinstance(payload.get("text_result"), dict) else {}
        if text_result.get("ok"):
            print(f"result_path={text_result.get('path', '')}")
            excerpt = str(text_result.get("excerpt") or "").strip()
            if excerpt:
                print("")
                print(excerpt)
    return 0 if payload.get("ok") else 1


def validate() -> dict[str, Any]:
    args = argparse.Namespace(
        task="search github repositories for local MCP gateway",
        target="mcp gateway",
        url="",
        path="",
        name="",
        intent="unknown",
        need_materialization=False,
        download=False,
        allow_network=True,
        allow_filesystem_write=False,
        max_bytes=None,
        sha256="",
        timeout=20,
        retries=1,
        target_dir="",
        auto_owner=True,
        owner_execution_mode="read_only",
        purpose="validate resource get payload",
        validation_profile="quick",
        fast=True,
        runtime="generic",
        download_backend="",
        resume_download=False,
        package_ecosystem="",
        package_action="",
        windows_package_manager="",
        package_id="",
        winget_id="",
        verify_binary="",
        install_approved=False,
        accept_winget_agreements=False,
    )
    payload = build_get_payload(args)
    request = payload.get("request") if isinstance(payload.get("request"), dict) else {}
    return {
        "schema": "resource_get.validate.v1",
        "ok": request.get("intent") == "external_dependency" and bool(request.get("auto_owner")),
        "intent": request.get("intent", ""),
        "auto_owner": bool(request.get("auto_owner")),
        "writes_remote_state": False,
    }
