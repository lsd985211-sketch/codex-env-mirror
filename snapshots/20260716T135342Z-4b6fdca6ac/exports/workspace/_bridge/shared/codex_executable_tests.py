from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_executable import codex_executable_candidates, discover_codex_executable


class CodexExecutableDiscoveryTests(unittest.TestCase):
    def _touch(self, path: Path, *, mtime: float | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"exe")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_latest_desktop_bin_beats_stale_baseline_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self._touch(root / "OpenAI" / "Codex" / "bin" / "old" / "codex.exe", mtime=time.time() - 60)
            latest = self._touch(root / "OpenAI" / "Codex" / "bin" / "new" / "codex.exe", mtime=time.time())
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps({"env": {"CODEX_CLI_PATH": str(root / "missing.exe")}}), encoding="utf-8")
            with patch("codex_executable.shutil.which", return_value=str(old)):
                selected = discover_codex_executable(local_appdata=root, startup_baseline=baseline, env={})
            self.assertEqual(Path(selected), latest)

    def test_explicit_existing_path_has_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit = self._touch(root / "configured" / "codex.exe")
            self._touch(root / "OpenAI" / "Codex" / "bin" / "new" / "codex.exe")
            selected = discover_codex_executable(explicit_path=str(explicit), local_appdata=root, env={})
            self.assertEqual(Path(selected), explicit)

    def test_stale_explicit_and_env_paths_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            latest = self._touch(root / "OpenAI" / "Codex" / "bin" / "new" / "codex.exe")
            rows = codex_executable_candidates(
                explicit_path=str(root / "stale-explicit.exe"),
                env={"CODEX_CLI_PATH": str(root / "stale-env.exe")},
                local_appdata=root,
            )
            self.assertEqual(rows[0]["source"], "desktop_latest_bin")
            self.assertEqual(Path(rows[0]["path"]), latest)


if __name__ == "__main__":
    unittest.main()
