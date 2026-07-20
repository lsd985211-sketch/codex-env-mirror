#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _bridge.shared.codex_desktop_package import (
    is_codex_cli_path,
    is_desktop_host_path,
    is_desktop_host_process,
    parse_manifest_entrypoint,
    resolve_entrypoint_from_install_location,
)


MANIFEST_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10">
  <Applications>
    <Application Id="App" Executable="{executable}" EntryPoint="Windows.FullTrustApplication" />
  </Applications>
</Package>
"""


class CodexDesktopPackageTests(unittest.TestCase):
    def test_manifest_resolves_current_chatgpt_host(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            executable = root / "app" / "ChatGPT.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            manifest = root / "AppxManifest.xml"
            manifest.write_text(MANIFEST_TEMPLATE.format(executable="app/ChatGPT.exe"), encoding="utf-8")

            parsed = parse_manifest_entrypoint(manifest)
            resolved = resolve_entrypoint_from_install_location(root)

            self.assertEqual(parsed, ("App", Path("app/ChatGPT.exe")))
            self.assertEqual(resolved, ("App", Path("app/ChatGPT.exe"), "manifest"))

    def test_manifest_resolves_legacy_codex_host(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            executable = root / "app" / "Codex.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            (root / "AppxManifest.xml").write_text(
                MANIFEST_TEMPLATE.format(executable="app\\Codex.exe"),
                encoding="utf-8",
            )

            self.assertEqual(
                resolve_entrypoint_from_install_location(root),
                ("App", Path("app/Codex.exe"), "manifest"),
            )

    def test_fallback_prefers_current_host_when_manifest_is_unusable(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            app = root / "app"
            app.mkdir(parents=True)
            (app / "ChatGPT.exe").touch()
            (app / "Codex.exe").touch()

            self.assertEqual(
                resolve_entrypoint_from_install_location(root),
                ("App", Path("app/ChatGPT.exe"), "fallback"),
            )

    def test_desktop_and_cli_paths_remain_distinct(self) -> None:
        host = r"C:\Program Files\WindowsApps\OpenAI.Codex_26.707.3748.0_x64__x\app\ChatGPT.exe"
        legacy = r"C:\Program Files\WindowsApps\OpenAI.Codex_1.0.0.0_x64__x\app\Codex.exe"
        cli = r"C:\Program Files\WindowsApps\OpenAI.Codex_26.707.3748.0_x64__x\app\resources\codex.exe"

        self.assertTrue(is_desktop_host_path(host))
        self.assertTrue(is_desktop_host_path(legacy))
        self.assertFalse(is_desktop_host_path(cli))
        self.assertTrue(is_codex_cli_path(cli))

    def test_main_process_filter_supports_both_host_names(self) -> None:
        base = r"C:\Program Files\WindowsApps\OpenAI.Codex_26.707.3748.0_x64__x\app"
        self.assertTrue(
            is_desktop_host_process(
                name="ChatGPT.exe",
                executable_path=base + r"\ChatGPT.exe",
                command_line='"ChatGPT.exe" --remote-debugging-port=9229',
                main_only=True,
            )
        )
        self.assertTrue(
            is_desktop_host_process(
                name="Codex.exe",
                executable_path=base + r"\Codex.exe",
                command_line='"Codex.exe" --remote-debugging-port=9229',
                main_only=True,
            )
        )
        self.assertFalse(
            is_desktop_host_process(
                name="ChatGPT.exe",
                executable_path=base + r"\ChatGPT.exe",
                command_line='"ChatGPT.exe" --type=renderer',
                main_only=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
