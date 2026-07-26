#!/usr/bin/env python3
"""Regression tests for the Codex Desktop protocol compatibility owner."""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

import codex_desktop_protocol_compatibility as compatibility  # noqa: E402


def write_asar(path: Path, assets: dict[str, bytes]) -> None:
    root: dict = {"files": {}}
    offset = 0
    for relative, payload in assets.items():
        node = root["files"]
        parts = relative.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {"files": {}})["files"]
        node[parts[-1]] = {
            "size": len(payload),
            "offset": str(offset),
            "integrity": {"algorithm": "SHA256", "hash": hashlib.sha256(payload).hexdigest()},
        }
        offset += len(payload)
    header_json = json.dumps(root, separators=(",", ":")).encode("utf-8")
    header = struct.pack("<4I", 4, len(header_json) + 9, len(header_json) + 5, len(header_json))
    path.write_bytes(header + header_json + b"\0" + b"".join(assets.values()))


class CodexDesktopProtocolCompatibilityTests(unittest.TestCase):
    def test_inspection_accepts_native_optout_while_vendor_migration_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "app.asar"
            write_asar(path, {
                ".vite/build/src-test.js": b"var O={deprecationNotice:!0};var A=Object.entries(O).filter(([e,t])=>!t).map(([e])=>e);var x={optOutNotificationMethods:A.slice()};",
                "webview/assets/app-main-test.js": b"await e.sendRequest(`thread/rollback`,{threadId:t,numTurns:1})",
            })
            result = compatibility.inspect_asar(path)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rollback_call_count"], 1)
        self.assertTrue(result["native_notice_suppression_declared"])
        self.assertTrue(result["upstream_migration_pending"])
        self.assertEqual(result["status"], "native_notice_optout_ready")

    def test_inspection_rejects_deprecated_call_without_native_optout(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "app.asar"
            write_asar(path, {
                ".vite/build/src-test.js": b"var x={optOutNotificationMethods:[]};",
                "webview/assets/app-main-test.js": b"await e.sendRequest(`thread/rollback`,{})",
            })
            result = compatibility.inspect_asar(path)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "deprecated_method_without_native_notice_optout")

    def test_inspection_accepts_completed_vendor_migration_without_optout(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "app.asar"
            write_asar(path, {"webview/assets/app-main-test.js": b"thread/start thread/resume thread/fork"})
            result = compatibility.inspect_asar(path)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["migration_complete"])
        self.assertFalse(result["upstream_migration_pending"])
        self.assertEqual(result["status"], "vendor_migration_complete")

    def test_isolated_probe_proves_optout_suppresses_exact_notice(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            server = Path(raw) / "fake_app_server.py"
            server.write_text(
                "import json, sys\n"
                "caps = {}\n"
                "for line in sys.stdin:\n"
                "    request = json.loads(line)\n"
                "    if request.get('method') == 'initialize':\n"
                "        caps = request['params'].get('capabilities', {})\n"
                "        print(json.dumps({'id': request['id'], 'result': {'ok': True}}), flush=True)\n"
                "    elif request.get('method') == 'thread/rollback':\n"
                "        if 'deprecationNotice' not in caps.get('optOutNotificationMethods', []):\n"
                "            print(json.dumps({'method': 'deprecationNotice', 'params': {'summary': 'thread/rollback is deprecated and will be removed soon', 'details': None}}), flush=True)\n"
                "        print(json.dumps({'id': request['id'], 'error': {'code': -32600, 'message': 'thread not found'}}), flush=True)\n",
                encoding="utf-8",
            )
            result = compatibility.probe_app_server([sys.executable, str(server)], timeout=1.0)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["without_optout"]["deprecation_notice_received"])
        self.assertFalse(result["with_optout"]["deprecation_notice_received"])


if __name__ == "__main__":
    unittest.main()
