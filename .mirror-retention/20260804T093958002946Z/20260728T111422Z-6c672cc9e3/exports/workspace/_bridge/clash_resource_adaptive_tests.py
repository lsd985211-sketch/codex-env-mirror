#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import yaml

import clash_mihomo_control as clash
import clash_node_metrics as metrics
from resource_download_backends import availability


class ClashResourceAdaptiveTests(unittest.TestCase):
    def test_multi_profile_snapshot_is_secret_free_and_dynamic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "profiles").mkdir()
            profiles = {
                "current": "one",
                "items": [
                    {"uid": "one", "type": "remote", "name": "A", "file": "one.yaml", "url": "secret-a"},
                    {"uid": "two", "type": "remote", "name": "B", "file": "two.yaml", "url": "secret-b"},
                ],
            }
            (root / "profiles.yaml").write_text(yaml.safe_dump(profiles), encoding="utf-8")
            (root / "profiles" / "one.yaml").write_text("proxies: []\n", encoding="utf-8")
            generated = {"proxy-groups": [{"name": "节点选择", "type": "select", "proxies": ["A"]}]}
            (root / "clash-verge.yaml").write_text(yaml.safe_dump(generated, allow_unicode=True), encoding="utf-8")
            snapshot = clash.verge_profile_snapshot(root)
            self.assertEqual(snapshot["remote_profile_count"], 2)
            self.assertEqual(snapshot["recommended_group"], "节点选择")
            self.assertNotIn("secret-a", str(snapshot))

    def test_default_metric_group_is_dynamic(self) -> None:
        self.assertEqual(metrics.DEFAULT_GROUP, "")

    def test_cache_signature_changes_with_current_profile(self) -> None:
        first = {"current_uid": "one", "config_digest": "a", "recommended_group": "节点选择"}
        second = {"current_uid": "two", "config_digest": "b", "recommended_group": "节点选择"}
        with patch.object(metrics.clash, "verge_profile_snapshot", return_value=first):
            first_signature = metrics.current_profile_signature()
        with patch.object(metrics.clash, "verge_profile_snapshot", return_value=second):
            second_signature = metrics.current_profile_signature()
        self.assertNotEqual(first_signature, second_signature)

    def test_cross_platform_tool_evidence_has_explicit_platform(self) -> None:
        result = availability().to_dict()
        self.assertIn(result["tools"]["curl"]["platform"], {"current_runtime", "windows_host"})
        self.assertIn("installed", result["tools"]["aria2c"])


if __name__ == "__main__":
    unittest.main()
