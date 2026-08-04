#!/usr/bin/env python3

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BRIDGE_ROOT = REPO_ROOT / "workspace" / "_bridge"


class BridgeImportCompatibilityTests(unittest.TestCase):
    def run_without_pythonpath(self, arguments: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        return subprocess.run(
            [sys.executable, *arguments],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    def run_from_repository_root(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return self.run_without_pythonpath(arguments, cwd=REPO_ROOT)

    def test_bridge_tests_do_not_require_top_level_package_imports(self) -> None:
        offenders: list[str] = []
        tracked = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=True,
        ).stdout.splitlines()
        test_paths = [
            REPO_ROOT / value
            for value in tracked
            if value.startswith("workspace/_bridge/") and value.endswith("_tests.py")
        ]
        for path in test_paths:
            source = path.read_text(encoding="utf-8")
            if re.search(r"^(?:from|import) _bridge(?:\.|\s|$)", source, flags=re.MULTILINE):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())

        self.assertEqual(offenders, [])

    def test_representative_test_imports_ignore_caller_directory(self) -> None:
        targets = [
            BRIDGE_ROOT / "skill_lifecycle_governance_tests.py",
            BRIDGE_ROOT / "shared" / "backup_router_tests.py",
        ]
        with tempfile.TemporaryDirectory() as directory:
            for target in targets:
                with self.subTest(target=target.name):
                    completed = self.run_without_pythonpath(
                        [str(target)],
                        cwd=Path(directory),
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)

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
