#!/usr/bin/env python3
"""Regression tests for structured resource delegation and execution mapping."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import resource_cli
from codex_resource_delegation import build_delegation, build_delegation_from_envelope
from resource_broker import candidate_tools, request_from_payload, route_for_request
from resource_collection_acquirer import _accept_unique_artifacts
from resource_source_strategy import classify_resource_kind
from resource_strategy_policy import request_requires_multi_source_research
from structured_task_envelope import normalize_resource_envelope, validate


def image_collection_envelope() -> dict:
    return normalize_resource_envelope(
        {
            "domain": "resource",
            "action": "discover_and_download",
            "summary": "下载十张不同的华为总部图片",
            "target": "华为总部",
            "resource": {
                "kind": "image",
                "quantity": {"requested": 10, "minimum": 10, "maximum": 10},
                "uniqueness": {
                    "required": True,
                    "dimensions": ["content", "viewpoint", "source_url"],
                    "deduplication_keys": ["canonical_url", "source_id"],
                },
                "source_policy": {
                    "mode": "multi_source",
                    "domains": ["commons.wikimedia.org", "openverse.org"],
                    "authority": "trusted",
                    "source_kind": "open_media",
                },
                "freshness": {"mode": "recent", "max_age_days": 3650},
                "materialization": {"required": True, "destination_policy": "user_resource_library"},
                "constraints": {"language": "zh", "format": "image", "license": "open", "exclude": ["logo"]},
                "owner_tools": {"preferred": ["resource_router"], "blocked": ["playwright"]},
                "quality": {"relevance_threshold": 0.65, "required_source_count": 2},
            },
            "safety": {"allow_network": True, "allow_filesystem_write": True},
        }
    )


class StructuredTaskEnvelopeTests(unittest.TestCase):
    def test_validator_and_explicit_quantity_conflict(self) -> None:
        self.assertTrue(validate()["ok"])
        conflict = normalize_resource_envelope(
            {
                "domain": "resource",
                "action": "discover",
                "summary": "find five images",
                "target": "headquarters",
                "resource": {"kind": "image", "quantity": {"requested": 10}},
            }
        )
        self.assertEqual(conflict["resource"]["quantity"]["requested"], 10)
        self.assertEqual(conflict["conflicts"][0]["reason"], "explicit_structured_field_precedence")

    def test_structured_fields_drive_broker_and_strategy(self) -> None:
        payload = build_delegation_from_envelope(image_collection_envelope())
        self.assertTrue(payload["ok"])
        request_payload = payload["request"]
        self.assertEqual(request_payload["metadata"]["requested_count"], 10)
        self.assertTrue(request_payload["metadata"]["uniqueness_required"])
        self.assertTrue(request_payload["metadata"]["multi_source_required"])
        self.assertIn("Codex资源库", request_payload["target_dir"])
        request = request_from_payload(request_payload)
        route = route_for_request(request)
        self.assertEqual(classify_resource_kind(request_payload, route.to_dict()), "image")
        self.assertNotIn("playwright", candidate_tools(route, request))
        self.assertTrue(request_requires_multi_source_research(request_payload))

    def test_low_confidence_text_cannot_authorize_install(self) -> None:
        payload = build_delegation(task="不要安装 package，只查文档", target="package docs")
        envelope = payload["request"]["metadata"]["task_envelope"]
        self.assertNotEqual(envelope["action"], "install")
        self.assertNotIn("install_approved", payload["request"]["metadata"])

    def test_explicit_package_contract_drives_install_policy(self) -> None:
        envelope = normalize_resource_envelope(
            {
                "domain": "resource",
                "action": "install",
                "summary": "Install aria2 through the resource layer",
                "target": "aria2",
                "resource": {
                    "kind": "package",
                    "package": {
                        "ecosystem": "windows_tool",
                        "manager": "choco",
                        "package_id": "aria2",
                        "verify_binary": "aria2c",
                    },
                },
                "safety": {"allow_network": True, "allow_filesystem_write": True, "install_approved": True},
            }
        )
        payload = build_delegation_from_envelope(envelope)
        metadata = payload["request"]["metadata"]
        self.assertEqual(metadata["package_action"], "install")
        self.assertEqual(metadata["package_ecosystem"], "windows_tool")
        self.assertEqual(metadata["windows_package_manager"], "choco")
        self.assertTrue(metadata["install_approved"])

    def test_custom_command_maps_collection_fields(self) -> None:
        request_json = json.dumps(image_collection_envelope(), ensure_ascii=False)
        args = resource_cli.build_parser().parse_args(["custom", "--request-json", request_json, "--json"])
        fake_result = {
            "ok": True,
            "status": "completed",
            "next_action": "consume_artifacts",
            "structured_execution": {"quantity_applied": 10, "uniqueness_applied": True},
            "artifacts": [],
        }
        with patch.object(resource_cli, "collect_resources", return_value=fake_result) as collect:
            with redirect_stdout(io.StringIO()):
                exit_code = resource_cli.command_custom(args)
        self.assertEqual(exit_code, 0)
        kwargs = collect.call_args.kwargs
        self.assertEqual(kwargs["count"], 10)
        self.assertTrue(kwargs["uniqueness_required"])
        self.assertEqual(kwargs["source_mode"], "multi_source")
        self.assertEqual(kwargs["max_age_days"], 3650)

    def test_content_hash_uniqueness_removes_owned_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            from pathlib import Path

            root = Path(temp_dir)
            duplicate = root / "duplicate.bin"
            duplicate.write_bytes(b"same")
            accepted, duplicates = _accept_unique_artifacts(
                [{"sha256": "abc", "artifact_path": str(root / "first.bin")}],
                [{"sha256": "abc", "artifact_path": str(duplicate)}],
                keys=["content_hash"],
                target_dir=root,
            )
            self.assertEqual(accepted, [])
            self.assertTrue(duplicates[0]["duplicate_artifact_removed"])
            self.assertFalse(duplicate.exists())


if __name__ == "__main__":
    unittest.main()
