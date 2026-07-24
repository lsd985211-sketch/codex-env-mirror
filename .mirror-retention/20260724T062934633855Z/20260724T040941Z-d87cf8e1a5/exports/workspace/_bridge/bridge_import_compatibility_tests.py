#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class BridgeImportCompatibilityTests(unittest.TestCase):
    def run_from_repository_root(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        return subprocess.run(
            [sys.executable, *arguments],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    def test_bridge_and_workspace_bridge_share_one_package_identity(self) -> None:
        completed = self.run_from_repository_root(
            [
                "-c",
                (
                    "import workspace._bridge as bridge; "
                    "import workspace._bridge.skill_lifecycle_governance_tests; "
                    "import _bridge; "
                    "assert _bridge is bridge"
                ),
            ]
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_three_test_modules_run_by_package_name_from_repository_root(self) -> None:
        completed = self.run_from_repository_root(
            [
                "-m",
                "unittest",
                "workspace._bridge.headroom_runtime_tests",
                "workspace._bridge.local_mcp_hub_headroom_tests",
                "workspace._bridge.local_mcp_stdio_client_tests",
            ]
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
