#!/usr/bin/env python3

from __future__ import annotations

import json
import unittest

from codex_node_repl_wsl_proxy import project_json_line, windows_file_uri


class NodeReplWslProxyTests(unittest.TestCase):
    def test_projects_linux_workspace_uri_to_wsl_unc_uri(self) -> None:
        self.assertEqual(
            windows_file_uri("file:///home/codexlab/work/codex workspace/中文", distribution="Codex-Wsl-Lab"),
            "file://wsl.localhost/Codex-Wsl-Lab/home/codexlab/work/codex%20workspace/%E4%B8%AD%E6%96%87",
        )

    def test_projects_mounted_windows_path_to_drive_uri(self) -> None:
        self.assertEqual(
            windows_file_uri("file:///mnt/c/Users/45543/Desktop", distribution="Codex-Wsl-Lab"),
            "file:///C:/Users/45543/Desktop",
        )

    def test_rewrites_only_sandbox_metadata(self) -> None:
        message = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "arguments": {"sandboxCwd": "file:///home/unchanged"},
                "_meta": {
                    "codex/sandbox-state-meta": {
                        "sandboxCwd": "file:///home/codexlab/work/codex-workspace",
                        "permissionProfile": "workspace-write",
                    }
                },
            },
        }
        projected = json.loads(project_json_line((json.dumps(message) + "\n").encode(), distribution="Codex-Wsl-Lab"))
        self.assertEqual(projected["params"]["arguments"]["sandboxCwd"], "file:///home/unchanged")
        self.assertEqual(
            projected["params"]["_meta"]["codex/sandbox-state-meta"]["sandboxCwd"],
            "file://wsl.localhost/Codex-Wsl-Lab/home/codexlab/work/codex-workspace",
        )

    def test_preserves_non_json_bytes(self) -> None:
        self.assertEqual(project_json_line(b"not-json\n", distribution="Codex-Wsl-Lab"), b"not-json\n")


if __name__ == "__main__":
    unittest.main()
