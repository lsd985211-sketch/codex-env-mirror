from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

import audio_toolkit  # noqa: E402


class AudioToolkitTests(unittest.TestCase):
    def test_validate_emits_machine_readable_read_only_receipt(self) -> None:
        output = io.StringIO()
        with patch.object(audio_toolkit, "require_tool", side_effect=lambda name: f"C:/tools/{name}.exe"):
            with patch.object(audio_toolkit, "module_available", return_value=False):
                with redirect_stdout(output):
                    returncode = audio_toolkit.cmd_validate(argparse.Namespace())

        payload = json.loads(output.getvalue())
        self.assertEqual(returncode, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["read_only"])
        self.assertEqual([item["name"] for item in payload["tools"]], ["ffmpeg", "ffprobe"])
        self.assertEqual(payload["issues"], [])

    def test_validate_reports_missing_required_tool(self) -> None:
        output = io.StringIO()

        def require_tool(name: str) -> str:
            if name == "ffprobe":
                raise SystemExit("missing ffprobe")
            return f"C:/tools/{name}.exe"

        with patch.object(audio_toolkit, "require_tool", side_effect=require_tool):
            with patch.object(audio_toolkit, "module_available", return_value=False):
                with redirect_stdout(output):
                    returncode = audio_toolkit.cmd_validate(argparse.Namespace())

        payload = json.loads(output.getvalue())
        self.assertEqual(returncode, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["issues"][0]["tool"], "ffprobe")


if __name__ == "__main__":
    unittest.main()
