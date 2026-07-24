#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from local_mcp_stdio_client import fresh_stdio_call


SERVER = r'''
import json, sys
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [
            {"name": "read", "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object"}},
            {"name": "cache", "inputSchema": {"type": "object"}},
        ]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": json.dumps(request["params"], sort_keys=True)}]}
    else:
        continue
    if "id" in request:
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
'''


class LocalMcpStdioClientTests(unittest.TestCase):
    def _server(self, root: Path) -> Path:
        path = root / "fake_mcp.py"
        path.write_text(textwrap.dedent(SERVER), encoding="utf-8")
        return path

    def test_initialize_list_and_readonly_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = fresh_stdio_call(
                command=[sys.executable, str(self._server(root))],
                working_directory=root,
                tool="read",
                arguments={"value": 1},
                timeout_seconds=5,
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["tool"], "read")

    def test_non_readonly_requires_target_owner_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            server = self._server(root)
            rejected = fresh_stdio_call(command=[sys.executable, str(server)], working_directory=root, tool="cache", timeout_seconds=5)
            accepted = fresh_stdio_call(
                command=[sys.executable, str(server)],
                working_directory=root,
                tool="cache",
                allowed_tools={"cache"},
                timeout_seconds=5,
            )
        self.assertEqual(rejected["reason"], "mcp_tool_is_not_read_only_or_allowlisted")
        self.assertTrue(accepted["ok"], accepted)


if __name__ == "__main__":
    unittest.main()
