#!/usr/bin/env python3
"""Focused contract tests for the read-only MTP media archive owner."""

from __future__ import annotations

import unittest

import mtp_media_archive_owner as owner
from shared.windows_powershell import decode_encoded_command, encoded_command_arguments


class MtpMediaArchiveOwnerTests(unittest.TestCase):
    def test_snapshot_uses_pipeline_materialization_and_encoded_transport(self) -> None:
        script = owner.snapshot_script("OPPO test")
        self.assertIn("ParseName($rootName)", script)
        self.assertNotIn("Get-MtpItems ($storage.GetFolder())", script)
        self.assertIn("writes_device = $false", script)
        encoded = encoded_command_arguments(script)[3]
        self.assertEqual(script, decode_encoded_command(encoded))

    def test_archive_plan_allows_only_public_wechat_roots(self) -> None:
        plan = owner.archive_plan("OPPO", "Tencent/MicroMsg", r"C:\Archive\WeChat")
        self.assertTrue(plan["ok"])
        self.assertFalse(plan["wechat_boundary"]["wechat_chat_history_restore"])
        rejected = owner.archive_plan("OPPO", "Android/data/com.tencent.mm", r"C:\Archive\WeChat")
        self.assertFalse(rejected["ok"])
        self.assertEqual("source_root_not_allowlisted", rejected["reason"])

    def test_archive_plan_rejects_relative_destination(self) -> None:
        payload = owner.archive_plan("OPPO", "Tencent/MicroMsg", r"..\Archive")
        self.assertFalse(payload["ok"])
        self.assertEqual("destination_must_be_absolute_without_traversal", payload["reason"])

    def test_video_archive_plan_is_fixed_and_has_no_unsafe_apply_backend(self) -> None:
        plan = owner.video_archive_plan("OPPO", r"C:\Users\45543\Desktop\Codex资源库\视频\OPPO\20260721")
        self.assertTrue(plan["ok"])
        self.assertTrue(plan["source_read_only"])
        self.assertFalse(plan["overwrite"])
        self.assertFalse(plan["apply_available"])
        self.assertEqual("headless_shell_copyhere_backend_forbidden", plan["blocked_backend_reason"])
        self.assertIn("DCIM", plan["source_roots"])
        self.assertIn(".mp4", plan["extensions"])

    def test_video_archive_plan_rejects_non_resource_destination(self) -> None:
        plan = owner.video_archive_plan("OPPO", r"C:\Temp\archive")
        self.assertFalse(plan["ok"])
        self.assertEqual("destination_not_resource_library", plan["reason"])


if __name__ == "__main__":
    unittest.main()
