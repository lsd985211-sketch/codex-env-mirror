#!/usr/bin/env python3
"""Tests for the youtube-feed resource owner adapter."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from resource_youtube_feed_owner import execute_youtube_feed, normalize_videos, request_days


GATEWAY_PLAN = {
    "ok": True,
    "plan": {
        "route_mode": "probe_selected_direct",
        "target_kind": "video",
        "env": {},
        "unset_env": [],
    },
}


class YoutubeFeedOwnerTests(unittest.TestCase):
    def test_normalize_videos_requires_url_or_video_id(self) -> None:
        items = normalize_videos(
            [
                {"title": "Valid", "video_id": "abc", "channel": "Example"},
                {"title": "Missing identity"},
            ]
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "https://www.youtube.com/watch?v=abc")

    def test_days_are_bounded(self) -> None:
        self.assertEqual(request_days({"metadata": {"constraints": {"days": 0}}}), 1)
        self.assertEqual(request_days({"metadata": {"constraints": {"days": 90}}}), 30)
        self.assertEqual(request_days({"metadata": {"constraints": {"days": "bad"}}}), 2)
        self.assertEqual(
            request_days(
                {
                    "metadata": {
                        "custom_delegation": {
                            "constraints": {"constraints": {"days": "5"}}
                        }
                    }
                }
            ),
            5,
        )

    def test_execute_normalizes_script_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "get_updates.py"
            script.write_text("# test fixture\n", encoding="utf-8")
            payload = [
                {
                    "channel": "Example",
                    "title": "New video",
                    "video_id": "xyz",
                    "published": "2026-07-12 10:00",
                    "summary": "Summary",
                }
            ]
            completed = subprocess.CompletedProcess(
                args=[], returncode=0, stdout=json.dumps(payload), stderr=""
            )
            with patch("resource_youtube_feed_owner.script_path", return_value=script), patch(
                "resource_youtube_feed_owner.subprocess.run", return_value=completed
            ):
                result = execute_youtube_feed(
                    {"metadata": {"constraints": {"days": 3}}}, GATEWAY_PLAN, timeout=10
                )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["metadata"]["result_count"], 1)
        self.assertEqual(result["candidates"][0]["source"], "youtube-feed")

    def test_execute_reports_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "get_updates.py"
            script.write_text("# test fixture\n", encoding="utf-8")
            with patch("resource_youtube_feed_owner.script_path", return_value=script), patch(
                "resource_youtube_feed_owner.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["python"], timeout=5),
            ):
                result = execute_youtube_feed({}, GATEWAY_PLAN, timeout=5)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_class"], "timeout")


if __name__ == "__main__":
    unittest.main()
