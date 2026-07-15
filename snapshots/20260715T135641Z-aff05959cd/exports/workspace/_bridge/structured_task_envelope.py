#!/usr/bin/env python3
"""Normalize structured resource task contracts for owner facades.

Ownership: pure schema normalization, provenance, conflict, and completeness
checks for machine-authored task envelopes.
Non-goals: route owners, execute jobs, persist state, infer permissions, or
replace domain-specific policy.
State behavior: read-only and deterministic.
Caller context: resource delegation builders, CLI/MCP facades, and validators.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SCHEMA = "structured_task_envelope.v1"
RESOURCE_ACTIONS = {"discover", "discover_and_download", "download", "inspect", "install", "materialize", "search"}
RESOURCE_KINDS = {"academic_paper", "audio", "dataset", "document", "documentation", "generic_download", "generic_web", "github_project", "image", "model_artifact", "package", "video"}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[str]:
    raw = value if isinstance(value, list | tuple | set) else ([value] if value not in (None, "") else [])
    return list(dict.fromkeys(text for item in raw if (text := str(item or "").strip())))


def _int(value: Any) -> int | None:
    try:
        return None if value in (None, "") else int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"", "0", "false", "no", "none", "off"}


def _unwrap(value: Any) -> tuple[Any, str, float]:
    if isinstance(value, dict) and "value" in value:
        return value.get("value"), str(value.get("source") or "explicit_field"), float(value.get("confidence", 1.0))
    return value, "explicit_field", 1.0


def _quantity_from_text(text: str) -> int | None:
    match = re.search(r"(?<!\d)(\d{1,4})\s*(?:张|份|篇|个|项|条|本|images?|photos?|papers?|files?|items?)", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    english = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    match = re.search(r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:images?|photos?|papers?|files?|items?)\b", text, re.IGNORECASE)
    if match:
        return english[match.group(1).lower()]
    chinese = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    match = re.search(r"([一二两三四五六七八九十])\s*(?:张|份|篇|个|项|条|本)", text)
    return chinese.get(match.group(1)) if match else None


def _resource_kind_from_text(text: str) -> str:
    lowered = text.lower()
    groups = (("academic_paper", ("论文", "paper", "journal", "arxiv", "doi")), ("image", ("图片", "照片", "image", "photo", "picture")), ("dataset", ("数据集", "dataset", "parquet", "jsonl")), ("documentation", ("文档", "documentation", "docs", "api reference")), ("github_project", ("github", "repository", "repo", "仓库")), ("package", ("package", "依赖", "软件包")), ("video", ("视频", "video")), ("audio", ("音频", "audio")))
    return next((kind for kind, terms in groups if any(term in lowered for term in terms)), "")


def normalize_resource_envelope(payload: dict[str, Any], *, strict: bool = True) -> dict[str, Any]:
    """Normalize a resource envelope without authorizing missing risky fields."""

    raw = _mapping(payload)
    resource = _mapping(raw.get("resource"))
    safety = _mapping(raw.get("safety"))
    provenance: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    errors: list[str] = []
    values = {
        "domain": _unwrap(raw.get("domain", "resource")), "action": _unwrap(raw.get("action", "")),
        "target": _unwrap(raw.get("target", "")), "summary": _unwrap(raw.get("summary", raw.get("task", ""))),
        "url": _unwrap(raw.get("url", "")), "path": _unwrap(raw.get("path", "")),
        "kind": _unwrap(resource.get("kind", raw.get("resource_kind", ""))),
    }
    domain = str(values["domain"][0] or "resource").strip()
    action = str(values["action"][0] or "").strip().lower()
    target = str(values["target"][0] or "").strip()
    summary = str(values["summary"][0] or "").strip()
    url = str(values["url"][0] or "").strip()
    path = str(values["path"][0] or "").strip()
    kind = str(values["kind"][0] or "").strip()
    if not kind and not strict:
        kind = _resource_kind_from_text(" ".join((summary, target)))
        values["kind"] = (kind, "deterministic_text_extraction", 0.72)
    quantity = _mapping(resource.get("quantity"))
    requested, minimum, maximum = _int(quantity.get("requested")), _int(quantity.get("minimum")), _int(quantity.get("maximum"))
    if requested is not None:
        minimum = requested if minimum is None else minimum
        maximum = requested if maximum is None else maximum
    natural_quantity = _quantity_from_text(" ".join((summary, target)))
    quantity_source, quantity_confidence = "explicit_field", 1.0
    if requested is None and natural_quantity is not None and not strict:
        requested = minimum = maximum = natural_quantity
        quantity_source, quantity_confidence = "deterministic_text_extraction", 0.78
    elif requested is not None and natural_quantity is not None and requested != natural_quantity:
        conflicts.append({"field": "/resource/quantity/requested", "winner": requested, "winner_source": "explicit_field", "ignored": natural_quantity, "ignored_source": "natural_language_observation", "reason": "explicit_structured_field_precedence"})
    uniqueness, source_policy = _mapping(resource.get("uniqueness")), _mapping(resource.get("source_policy"))
    freshness, materialization = _mapping(resource.get("freshness")), _mapping(resource.get("materialization"))
    constraints, owner_tools, quality = _mapping(resource.get("constraints")), _mapping(resource.get("owner_tools")), _mapping(resource.get("quality"))
    transfer, package = _mapping(resource.get("transfer")), _mapping(resource.get("package"))
    normalized = {
        "schema": SCHEMA, "domain": domain, "action": action, "summary": summary, "target": target, "url": url, "path": path,
        "resource": {
            "kind": kind, "quantity": {"requested": requested, "minimum": minimum, "maximum": maximum},
            "uniqueness": {"required": _bool(uniqueness.get("required")), "dimensions": _list(uniqueness.get("dimensions")), "deduplication_keys": _list(uniqueness.get("deduplication_keys"))},
            "source_policy": {"mode": str(source_policy.get("mode") or "").strip(), "domains": _list(source_policy.get("domains")), "authority": str(source_policy.get("authority") or "").strip(), "source_kind": str(source_policy.get("source_kind") or "").strip()},
            "freshness": {"mode": str(freshness.get("mode") or "").strip(), "max_age_days": _int(freshness.get("max_age_days"))},
            "materialization": {"required": _bool(materialization.get("required")), "destination_policy": str(materialization.get("destination_policy") or "").strip(), "target_dir": str(materialization.get("target_dir") or "").strip()},
            "constraints": {"language": str(constraints.get("language") or "").strip(), "format": str(constraints.get("format") or "").strip(), "license": str(constraints.get("license") or "").strip(), "exclude": _list(constraints.get("exclude")), "extra": _mapping(constraints.get("extra"))},
            "owner_tools": {"preferred": _list(owner_tools.get("preferred")), "blocked": _list(owner_tools.get("blocked"))},
            "quality": {"relevance_threshold": _float(quality.get("relevance_threshold")), "required_source_count": _int(quality.get("required_source_count"))},
            "transfer": {
                "name": str(transfer.get("name") or "").strip(),
                "max_bytes": _int(transfer.get("max_bytes")),
                "expected_sha256": str(transfer.get("expected_sha256") or "").strip(),
                "timeout_seconds": _int(transfer.get("timeout_seconds")),
                "retry_budget": _int(transfer.get("retry_budget")),
                "download_backend": str(transfer.get("download_backend") or "").strip(),
                "resume_download": _bool(transfer.get("resume_download")),
            },
            "package": {
                "ecosystem": str(package.get("ecosystem") or "").strip(),
                "manager": str(package.get("manager") or "").strip(),
                "package_id": str(package.get("package_id") or "").strip(),
                "winget_id": str(package.get("winget_id") or "").strip(),
                "verify_binary": str(package.get("verify_binary") or "").strip(),
                "accept_agreements": _bool(package.get("accept_agreements")),
            },
        },
        "safety": {"allow_network": _bool(safety.get("allow_network"), True), "allow_filesystem_write": _bool(safety.get("allow_filesystem_write")), "install_approved": _bool(safety.get("install_approved")), "remote_write_approved": _bool(safety.get("remote_write_approved"))},
        "provenance": provenance, "conflicts": conflicts, "missing_fields": [], "errors": errors,
    }
    for key, pointer in (("domain", "/domain"), ("action", "/action"), ("target", "/target"), ("summary", "/summary"), ("url", "/url"), ("path", "/path"), ("kind", "/resource/kind")):
        value, source, confidence = values[key]
        if str(value or "").strip():
            provenance[pointer] = {"source": source, "confidence": round(float(confidence), 3), "validated": True}
    if requested is not None:
        provenance["/resource/quantity/requested"] = {"source": quantity_source, "confidence": quantity_confidence, "validated": True}
    missing: list[str] = []
    if domain != "resource":
        errors.append("unsupported_domain")
    if not action:
        missing.append("action")
    elif action not in RESOURCE_ACTIONS:
        errors.append("unsupported_action")
    if not any((target, url, path)):
        missing.append("target_or_url_or_path")
    if kind and kind not in RESOURCE_KINDS:
        errors.append("unsupported_resource_kind")
    for value, label in ((requested, "requested"), (minimum, "minimum"), (maximum, "maximum")):
        if value is not None and value < 1:
            errors.append(f"invalid_quantity_{label}")
    if minimum is not None and maximum is not None and minimum > maximum:
        errors.append("invalid_quantity_range")
    if action == "install" and not normalized["safety"]["install_approved"]:
        errors.append("install_requires_explicit_install_approved")
    normalized.update({"missing_fields": missing, "complete": not missing and not errors, "ok": not missing and not errors, "mode": "strict_structured" if strict else "legacy_compatible"})
    return normalized


def build_legacy_resource_envelope(*, task: str, target: str, url: str, path: str, resource_kind: str, package_action: str, need_materialization: bool, allow_network: bool, allow_filesystem_write: bool, install_approved: bool, candidate_review: bool, quantity: int | None = None, minimum_quantity: int | None = None, maximum_quantity: int | None = None, uniqueness_required: bool = False, uniqueness_dimensions: list[str] | None = None, deduplication_keys: list[str] | None = None, source_mode: str = "", source_domains: list[str] | None = None, source_kind: str = "", authority: str = "", freshness_mode: str = "", max_age_days: int | None = None, target_dir: str = "", destination_policy: str = "", language: str = "", file_format: str = "", license_filter: str = "", exclude: list[str] | None = None, preferred_owner_tools: list[str] | None = None, blocked_owner_tools: list[str] | None = None, relevance_threshold: float | None = None, required_source_count: int | None = None) -> dict[str, Any]:
    explicit_target = str(target or url or path or "").strip()
    target_source = "explicit_field"
    if not explicit_target:
        explicit_target, target_source = str(task or "").strip(), "deterministic_text_extraction"
    action = str(package_action or "").strip().lower()
    if not action:
        action = "discover" if candidate_review else ("materialize" if need_materialization and (url or path) else ("discover_and_download" if need_materialization else "discover"))
    return normalize_resource_envelope({
        "domain": "resource", "action": action, "summary": task,
        "target": {"value": explicit_target, "source": target_source, "confidence": 1.0 if target_source == "explicit_field" else 0.72}, "url": url, "path": path,
        "resource": {
            "kind": resource_kind, "quantity": {"requested": quantity, "minimum": minimum_quantity, "maximum": maximum_quantity},
            "uniqueness": {"required": uniqueness_required, "dimensions": uniqueness_dimensions or [], "deduplication_keys": deduplication_keys or []},
            "source_policy": {"mode": source_mode, "domains": source_domains or [], "authority": authority, "source_kind": source_kind},
            "freshness": {"mode": freshness_mode, "max_age_days": max_age_days},
            "materialization": {"required": need_materialization, "destination_policy": destination_policy, "target_dir": target_dir},
            "constraints": {"language": language, "format": file_format, "license": license_filter, "exclude": exclude or []},
            "owner_tools": {"preferred": preferred_owner_tools or [], "blocked": blocked_owner_tools or []},
            "quality": {"relevance_threshold": relevance_threshold, "required_source_count": required_source_count},
        },
        "safety": {"allow_network": allow_network, "allow_filesystem_write": allow_filesystem_write, "install_approved": install_approved},
    }, strict=False)


def load_resource_envelope(*, request_json: str = "", request_file: str = "") -> dict[str, Any]:
    if request_json and request_file:
        return {"schema": SCHEMA, "ok": False, "complete": False, "errors": ["request_json_and_request_file_are_mutually_exclusive"], "missing_fields": []}
    if request_file:
        payload = json.loads(Path(request_file).expanduser().resolve().read_text(encoding="utf-8"))
    elif request_json:
        payload = json.loads(request_json)
    else:
        return {"schema": SCHEMA, "ok": False, "complete": False, "errors": ["missing_structured_request"], "missing_fields": []}
    return normalize_resource_envelope(payload, strict=True) if isinstance(payload, dict) else {"schema": SCHEMA, "ok": False, "complete": False, "errors": ["structured_request_must_be_object"], "missing_fields": []}


def resource_contract_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    envelope = _mapping(_mapping(metadata).get("task_envelope"))
    return envelope if envelope.get("schema") == SCHEMA and envelope.get("domain") == "resource" else {}


def resource_task_facts(envelope: dict[str, Any]) -> dict[str, bool]:
    """Return execution facts from normalized fields; natural-language summary is never consulted."""
    data = _mapping(envelope)
    if data.get("schema") != SCHEMA or data.get("domain") != "resource":
        return {}
    resource = _mapping(data.get("resource"))
    materialization = _mapping(resource.get("materialization"))
    safety = _mapping(data.get("safety"))
    action = str(data.get("action") or "").strip().lower()
    materialize = _bool(materialization.get("required")) or action in {"download", "materialize", "discover_and_download"}
    filesystem_write = materialize and _bool(safety.get("allow_filesystem_write"))
    package_install = action in {"install", "uninstall", "upgrade"}
    return {
        "external_network_read": _bool(safety.get("allow_network"), True),
        "resource_materialization": materialize,
        "local_write": filesystem_write,
        "package_install": package_install,
        "config_change": package_install,
        "durable_closeout_required": filesystem_write or package_install,
    }


def validate() -> dict[str, Any]:
    ten_images = normalize_resource_envelope({"domain": "resource", "action": "discover_and_download", "summary": "下载十张不同的华为总部图片", "target": "华为总部", "resource": {"kind": "image", "quantity": {"requested": 10}, "uniqueness": {"required": True, "dimensions": ["content", "viewpoint"], "deduplication_keys": ["content_hash", "canonical_url"]}, "materialization": {"required": True, "destination_policy": "user_resource_library"}}, "safety": {"allow_network": True, "allow_filesystem_write": True}})
    conflict = normalize_resource_envelope({"domain": "resource", "action": "discover", "summary": "find five images", "target": "headquarters", "resource": {"kind": "image", "quantity": {"requested": 10}}})
    missing = normalize_resource_envelope({"domain": "resource", "summary": "something"})
    unsafe_install = normalize_resource_envelope({"domain": "resource", "action": "install", "target": "aria2", "resource": {"kind": "package"}})
    ok = ten_images.get("ok") and bool(conflict.get("conflicts")) and set(missing.get("missing_fields") or []) == {"action", "target_or_url_or_path"} and "install_requires_explicit_install_approved" in (unsafe_install.get("errors") or [])
    return {"schema": "structured_task_envelope.validate.v1", "ok": ok, "cases": {"ten_images": ten_images, "conflict": conflict, "missing": missing, "unsafe_install": unsafe_install}}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))
