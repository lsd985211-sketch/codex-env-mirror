#!/usr/bin/env python3
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from shared.windows_runtime_assets import (
    gui_ocr_pip_cache_path,
    gui_ocr_python_paths,
    gui_ocr_runtime_root,
    openclaw_install_root,
    openclaw_node_path,
    openclaw_reply_script_path,
    openclaw_runtime_root,
    openclaw_state_path,
    windows_codex_runtime_root,
    windows_pip_cache_root,
)


class WindowsRuntimeAssetsTests(unittest.TestCase):
    def test_ocr_runtime_uses_managed_local_appdata_root(self) -> None:
        with patch.dict(os.environ, {"LOCALAPPDATA": r"D:\Local"}, clear=True):
            root = gui_ocr_runtime_root()
            primary, fallback = gui_ocr_python_paths()
        self.assertEqual(root, Path(r"D:\Local") / "Codex" / "runtimes" / "ocr")
        self.assertEqual(primary, root / "gpu-venv" / "Scripts" / "python.exe")
        self.assertEqual(fallback, root / "cpu-venv" / "Scripts" / "python.exe")

    def test_explicit_paths_override_defaults(self) -> None:
        env = {
            "GUI_OCR_RUNTIME_ROOT": r"D:\OCR",
            "GUI_OCR_PYTHON": r"D:\GPU\python.exe",
            "GUI_OCR_FALLBACK_PYTHON": r"D:\CPU\python.exe",
        }
        with patch.dict(os.environ, env, clear=True):
            primary, fallback = gui_ocr_python_paths()
        self.assertEqual(primary, Path(env["GUI_OCR_PYTHON"]))
        self.assertEqual(fallback, Path(env["GUI_OCR_FALLBACK_PYTHON"]))

    def test_reusable_cache_has_one_authority_and_one_ocr_projection(self) -> None:
        with patch.dict(os.environ, {"LOCALAPPDATA": r"D:\Local"}, clear=True):
            self.assertEqual(windows_pip_cache_root(), Path(r"D:\Local") / "pip" / "cache")
            self.assertEqual(gui_ocr_pip_cache_path(), gui_ocr_runtime_root() / "pip-cache")

    def test_openclaw_runtime_is_outside_the_retired_project_tree(self) -> None:
        with patch.dict(os.environ, {"LOCALAPPDATA": r"D:\Local"}, clear=True):
            root = openclaw_runtime_root()
            self.assertEqual(windows_codex_runtime_root(), Path(r"D:\Local") / "Codex")
            self.assertEqual(root, Path(r"D:\Local") / "Codex" / "openclaw")
            self.assertEqual(openclaw_install_root(), root / "clean-install")
            self.assertEqual(openclaw_state_path(), root / "clean-install" / "state")
            self.assertEqual(openclaw_reply_script_path(), root / "weixin_send_reply.mjs")
            self.assertEqual(openclaw_node_path(), root / "node24" / "node-v24.17.0-win-x64" / "node.exe")

    def test_openclaw_and_cache_paths_allow_managed_overrides(self) -> None:
        env = {
            "CODEX_WINDOWS_RUNTIME_ROOT": r"E:\CodexRuntime",
            "CODEX_WINDOWS_PIP_CACHE_ROOT": r"E:\PackageCache\pip",
            "CODEX_OPENCLAW_RUNTIME_ROOT": r"E:\OpenClaw",
            "CODEX_OPENCLAW_NODE": r"E:\Node\node.exe",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(windows_codex_runtime_root(), Path(env["CODEX_WINDOWS_RUNTIME_ROOT"]))
            self.assertEqual(windows_pip_cache_root(), Path(env["CODEX_WINDOWS_PIP_CACHE_ROOT"]))
            self.assertEqual(openclaw_runtime_root(), Path(env["CODEX_OPENCLAW_RUNTIME_ROOT"]))
            self.assertEqual(openclaw_node_path(), Path(env["CODEX_OPENCLAW_NODE"]))


if __name__ == "__main__":
    unittest.main()
