#!/usr/bin/env python3
"""Read-only memory surface snapshot and schema issue checks.

Owns: resource-library memory file summaries, schema/policy issue
classification, and the combined snapshot used by memory governance.
Non-goals: note absorption, PMB organization, memory writes, approval
application, or CLI argument parsing.
State behavior: reads JSON/filesystem metadata only; never writes local state.
Normal callers: `memory_governance.py` facade functions and validation flows.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


JsonDict = dict[str, Any]


def file_info(path: Path) -> JsonDict:
    exists = path.exists()
    data: JsonDict = {"path": str(path), "exists": exists}
    if exists:
        stat = path.stat()
        data.update(
            {
                "size_bytes": stat.st_size,
                "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            }
        )
    return data


def read_json(path: Path) -> tuple[JsonDict, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return {}, "json_root_not_object"
    return payload, ""


def missing_keys(payload: JsonDict, required: list[str]) -> list[str]:
    return [key for key in required if key not in payload]


def manifest_issues(manifest: JsonDict, error: str, *, absorption_index_path: Path) -> list[JsonDict]:
    issues: list[JsonDict] = []
    if error:
        issues.append({"code": "manifest_unreadable", "error": error})
    else:
        required = ["schema", "authority", "primary_store", "entrypoints", "namespaces", "policies", "maintenance_contract"]
        missing = missing_keys(manifest, required)
        if missing:
            issues.append({"code": "manifest_missing_required_keys", "missing": missing})
        if manifest.get("schema") != "codex-local-memory-manifest/v1":
            issues.append({"code": "manifest_schema_mismatch", "actual": manifest.get("schema")})
        if not isinstance(manifest.get("namespaces"), list) or not manifest.get("namespaces"):
            issues.append({"code": "manifest_namespaces_missing_or_empty"})
        entrypoints = manifest.get("entrypoints") if isinstance(manifest.get("entrypoints"), dict) else {}
        if str(entrypoints.get("absorption_index") or "") != str(absorption_index_path):
            issues.append({"code": "manifest_absorption_index_entry_missing_or_drifted"})
        policies = manifest.get("policies") if isinstance(manifest.get("policies"), dict) else {}
        if policies.get("secrets_in_normal_memory") is not False:
            issues.append({"code": "manifest_secret_policy_not_false"})
    return issues


def user_profile_issues(profile: JsonDict, error: str) -> list[JsonDict]:
    issues: list[JsonDict] = []
    if error:
        issues.append({"code": "profile_unreadable", "error": error})
    else:
        missing = missing_keys(profile, ["schema", "profile_id", "updated_at", "privacy_class", "secret_storage_policy", "facts"])
        if missing:
            issues.append({"code": "profile_missing_required_keys", "missing": missing})
        if profile.get("schema") != "codex-user-profile/v1":
            issues.append({"code": "profile_schema_mismatch", "actual": profile.get("schema")})
        if not isinstance(profile.get("facts"), list):
            issues.append({"code": "profile_facts_not_list"})
        secret_policy = profile.get("secret_storage_policy") if isinstance(profile.get("secret_storage_policy"), dict) else {}
        if secret_policy.get("normal_memory_may_store_secrets") is not False:
            issues.append({"code": "profile_secret_policy_not_false"})
    return issues


def memory_policy_issues(policy: JsonDict, error: str) -> list[JsonDict]:
    issues: list[JsonDict] = []
    if error:
        issues.append({"code": "policy_unreadable", "error": error})
    elif policy.get("schema") != "codex-memory-policy/v1":
        issues.append({"code": "policy_schema_mismatch", "actual": policy.get("schema")})
    return issues


def absorption_index_issues(absorption: JsonDict, error: str) -> list[JsonDict]:
    issues: list[JsonDict] = []
    if error:
        issues.append({"code": "absorption_index_unreadable", "error": error})
    else:
        missing = missing_keys(absorption, ["schema", "authority", "layers", "merged_themes", "pmb_review", "absorption_policy"])
        if missing:
            issues.append({"code": "absorption_index_missing_required_keys", "missing": missing})
        if absorption.get("schema") != "codex-memory-absorption-index/v1":
            issues.append({"code": "absorption_index_schema_mismatch", "actual": absorption.get("schema")})
        if not isinstance(absorption.get("merged_themes"), list) or not absorption.get("merged_themes"):
            issues.append({"code": "absorption_index_themes_missing_or_empty"})
        absorption_policy = absorption.get("absorption_policy") if isinstance(absorption.get("absorption_policy"), dict) else {}
        if absorption_policy.get("default_action") != "no_delete":
            issues.append({"code": "absorption_index_delete_policy_not_safe"})
    return issues


def build_snapshot(
    *,
    resource_memory_root: Path,
    memory_manifest: Path,
    user_profile: Path,
    memory_policy: Path,
    memory_absorption_index: Path,
    memory_manifest_schema: Path,
    user_profile_schema: Path,
    build_user_profile_guidance: Callable[[JsonDict], JsonDict],
    external_knowledge_snapshot: Callable[[], JsonDict],
    external_knowledge_doctor: Callable[[], JsonDict],
) -> JsonDict:
    manifest, manifest_error = read_json(memory_manifest) if memory_manifest.exists() else ({}, "missing")
    profile, profile_error = read_json(user_profile) if user_profile.exists() else ({}, "missing")
    policy, policy_error = read_json(memory_policy) if memory_policy.exists() else ({}, "missing")
    absorption, absorption_error = read_json(memory_absorption_index) if memory_absorption_index.exists() else ({}, "missing")

    manifest_issue_list = manifest_issues(manifest, manifest_error, absorption_index_path=memory_absorption_index)
    profile_issue_list = user_profile_issues(profile, profile_error)
    policy_issue_list = memory_policy_issues(policy, policy_error)
    absorption_issue_list = absorption_index_issues(absorption, absorption_error)

    return {
        "resource_memory_root": file_info(resource_memory_root),
        "manifest": {
            **file_info(memory_manifest),
            "ok": not manifest_issue_list,
            "schema": manifest.get("schema"),
            "namespace_count": len(manifest.get("namespaces") or []) if isinstance(manifest.get("namespaces"), list) else 0,
            "issues": manifest_issue_list,
        },
        "user_profile": {
            **file_info(user_profile),
            "ok": not profile_issue_list,
            "schema": profile.get("schema"),
            "fact_count": len(profile.get("facts") or []) if isinstance(profile.get("facts"), list) else 0,
            "guidance": build_user_profile_guidance(profile),
            "issues": profile_issue_list,
        },
        "policy": {
            **file_info(memory_policy),
            "ok": not policy_issue_list,
            "schema": policy.get("schema"),
            "issues": policy_issue_list,
        },
        "absorption_index": {
            **file_info(memory_absorption_index),
            "ok": not absorption_issue_list,
            "schema": absorption.get("schema"),
            "theme_count": len(absorption.get("merged_themes") or []) if isinstance(absorption.get("merged_themes"), list) else 0,
            "issues": absorption_issue_list,
        },
        "schemas": {
            "manifest": file_info(memory_manifest_schema),
            "user_profile": file_info(user_profile_schema),
        },
        "external_knowledge": {
            "snapshot": external_knowledge_snapshot(),
            "doctor": external_knowledge_doctor(),
        },
    }
