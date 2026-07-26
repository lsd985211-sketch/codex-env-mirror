#!/usr/bin/env python3
"""Focused regressions for the WSL PMB user-service owner."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import local_pmb_memory_service as owner


class LocalPmbMemoryServiceTests(unittest.TestCase):
    def test_unit_is_loopback_persistent_and_wsl_native(self) -> None:
        with patch.object(owner, "PRIMARY_ROOT", Path("/home/codexlab/work space")):
            content = owner.unit_content(
                executable=Path("/home/codexlab/pmb/bin/pmb"),
                fastembed_cache=Path("/home/codexlab/pmb/cache/fastembed"),
            )
        self.assertIn("daemon run --host 127.0.0.1 --port 8765 --idle-exit-min 0", content)
        self.assertIn(r"WorkingDirectory=/home/codexlab/work\x20space", content)
        self.assertNotIn('WorkingDirectory="', content)
        self.assertIn("Restart=on-failure", content)
        self.assertIn("NoNewPrivileges=yes", content)
        self.assertIn('Environment=FASTEMBED_CACHE_PATH="/home/codexlab/pmb/cache/fastembed"', content)
        self.assertNotIn("/mnt/c/", content)

    def test_cache_prepare_is_private_and_persistent(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as raw:
            cache = Path(raw) / "runtime" / "fastembed"
            with patch.object(owner, "PMB_FASTEMBED_CACHE", cache):
                payload = owner.ensure_cache_dir()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["persistent"])
        self.assertTrue(payload["private"])
        self.assertEqual("700", payload["mode"])

    def test_tmp_cache_is_not_accepted_as_persistent(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            cache = Path(raw) / "fastembed"
            cache.mkdir(mode=0o700)
            with patch.object(owner, "PMB_FASTEMBED_CACHE", cache):
                payload = owner.cache_status()
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["persistent"])

    def test_plan_declares_controlled_takeover_of_registered_daemon(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            executable = Path(raw) / "pmb"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o700)
            unit = Path(raw) / "codex-pmb-memory.service"
            with patch.object(owner, "PMB_EXE", executable), patch.object(owner, "unit_path", return_value=unit), patch.object(
                owner, "daemon_probe", return_value={"ok": True, "running": True, "pid": 42}
            ), patch.object(owner, "unit_status", return_value={"active": False}):
                payload = owner.plan()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["takeover"]["required"])
        self.assertEqual(42, payload["takeover"]["registered_pid"])

    def test_install_requires_exact_confirmation_before_state_change(self) -> None:
        with patch.object(owner, "plan", return_value={"ok": True, "blockers": []}), patch.object(
            owner, "install_user_unit"
        ) as installer:
            payload = owner.install("")
        self.assertFalse(payload["ok"])
        self.assertEqual("explicit_confirmation_required", payload["reason"])
        installer.assert_not_called()

    def test_validate_rejects_a_second_daemon_identity(self) -> None:
        planned = {"blockers": [], "unit_sha256": "same", "installed_unit_sha256": "same"}
        current = {
            "unit_exists": True,
            "enabled": True,
            "active": True,
            "daemon": {"running": True},
            "daemon_processes": {"count": 1},
            "identity": {"matches": False, "root_or_system": False},
        }
        with patch.object(owner, "plan", return_value=planned), patch.object(owner, "status", return_value=current):
            payload = owner.validate()
        self.assertFalse(payload["ok"])
        self.assertIn("pmb_daemon_identity_mismatch", {item["code"] for item in payload["issues"]})

    def test_validate_rejects_running_but_cold_daemon(self) -> None:
        planned = {"blockers": [], "unit_sha256": "same", "installed_unit_sha256": "same"}
        current = {
            "unit_exists": True,
            "enabled": True,
            "active": True,
            "daemon": {"running": True, "warm": False},
            "daemon_processes": {"count": 1},
            "identity": {"matches": True, "root_or_system": False},
            "fastembed_cache": {"ok": True},
        }
        with patch.object(owner, "plan", return_value=planned), patch.object(owner, "status", return_value=current):
            payload = owner.validate()
        self.assertFalse(payload["ok"])
        self.assertIn("pmb_daemon_not_warm", {item["code"] for item in payload["issues"]})


if __name__ == "__main__":
    unittest.main()
